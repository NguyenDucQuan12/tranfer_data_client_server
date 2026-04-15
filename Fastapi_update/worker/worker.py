"""
worker.py
---------
Worker nâng cấp với retry / delayed retry / DLQ / metrics / tracing.

Flow mới:
1. Worker kéo job từ main queue
2. Nếu xử lý lỗi:
   - chưa quá max retry -> schedule retry vào retry zset
   - quá max retry -> đẩy vào dead-letter queue
3. Worker định kỳ kéo job đến hạn retry trở lại queue chính
4. Worker ghi event vào stream user và tenant để notification service phát ra websocket
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import redis
from prometheus_client import start_http_server

from shared.config import settings
from shared.db import init_db, update_job_record
from shared.events import emit_job_event_sync
from shared.logging_config import configure_logging, get_logger
from shared.metrics import JOBS_COMPLETED_TOTAL, JOBS_FAILED_TOTAL, JOB_DLQ_TOTAL, JOB_PROCESSING_SECONDS, JOB_RETRIES_TOTAL
from shared.storage import get_storage_backend
from shared.tracing import clear_trace_context, set_trace_context
from worker.processor import analyze_bytes

configure_logging()
logger = get_logger('worker')
storage = get_storage_backend()



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



def retry_delay_seconds(attempt_number: int) -> int:
    """
    Exponential backoff đơn giản:
    attempt 1 -> base
    attempt 2 -> 2 * base
    attempt 3 -> 4 * base
    """
    return settings.RETRY_BASE_SECONDS * (2 ** max(0, attempt_number - 1))



def schedule_retry(redis_client: redis.Redis, job_payload: dict, error: str) -> None:
    """
    Đưa job vào retry zset với score = thời điểm được phép chạy lại.

    Lưu ý: `job_payload['attempts']` ở đây đã là số lần retry mới nhất,
    nên hàm này không tăng thêm nữa để tránh lệch số lần thử.
    """
    current_attempt = int(job_payload['attempts'])
    job_payload['last_error'] = error
    retry_at = time.time() + retry_delay_seconds(current_attempt)
    redis_client.zadd(settings.RETRY_ZSET_KEY, {json.dumps(job_payload, ensure_ascii=False): retry_at})



def move_due_retries_back_to_main_queue(redis_client: redis.Redis, limit: int = 100) -> int:
    """
    Hàm này là phần chính cho retry delayed.

    Worker không sleep chờ từng job retry.
    Thay vào đó, nó để retry job trong Sorted Set với score = retry_at.
    Mỗi vòng lặp, worker quét các job đến hạn và đẩy lại queue chính.
    """
    now = time.time()
    due_items = redis_client.zrangebyscore(settings.RETRY_ZSET_KEY, min=0, max=now, start=0, num=limit)
    if not due_items:
        return 0

    moved = 0
    pipe = redis_client.pipeline()
    for raw in due_items:
        pipe.zrem(settings.RETRY_ZSET_KEY, raw)
        pipe.lpush(settings.QUEUE_KEY, raw)
        moved += 1
    pipe.execute()
    return moved



def send_to_dead_letter_queue(redis_client: redis.Redis, job_payload: dict, error: str) -> None:
    dead_payload = {
        **job_payload,
        'dead_lettered_at': utc_now_iso(),
        'last_error': error,
    }
    redis_client.lpush(settings.DLQ_KEY, json.dumps(dead_payload, ensure_ascii=False))



def process_one_job(redis_client: redis.Redis, job_payload: dict) -> None:
    job_id = job_payload['job_id']
    tenant_id = job_payload['tenant_id']
    user_id = job_payload['user_id']
    filename = job_payload['filename']
    upload_object_key = job_payload['upload_object_key']
    attempts = int(job_payload.get('attempts', 0))
    trace_id = job_payload.get('trace_id', '-')

    set_trace_context(trace_id=trace_id, tenant_id=tenant_id, user_id=user_id, job_id=job_id)
    start = time.perf_counter()

    try:
        logger.info('Worker picked up job')
        update_job_record_sync(job_id, status='processing', progress=10, message='Worker đã nhận job', attempts=attempts)
        emit_job_event_sync(
            redis_client,
            event_type='job_processing',
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=job_id,
            status='processing',
            message='Worker đã nhận job',
            progress=10,
            trace_id=trace_id,
            attempts=attempts,
            filename=filename,
        )

        raw = storage.read_bytes(upload_object_key)
        analysis_result = analyze_bytes(raw, filename)

        update_job_record_sync(job_id, status='processing', progress=80, message='Đang ghi file kết quả', attempts=attempts)
        emit_job_event_sync(
            redis_client,
            event_type='job_progress',
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=job_id,
            status='processing',
            message='Đang lưu kết quả',
            progress=80,
            trace_id=trace_id,
            attempts=attempts,
            filename=filename,
        )

        result_payload = {
            'job_id': job_id,
            'tenant_id': tenant_id,
            'user_id': user_id,
            'source_file': filename,
            'processed_at': utc_now_iso(),
            'analysis': analysis_result,
            'attempts': attempts,
            'trace_id': trace_id,
        }
        result_key = f'tenant/{tenant_id}/user/{user_id}/results/{job_id}.json'
        storage.save_bytes(data=json.dumps(result_payload, ensure_ascii=False, indent=2).encode('utf-8'), object_key=result_key)

        update_job_record_sync(job_id, status='success', progress=100, message='Job xử lý thành công', result_object_key=result_key, error=None, attempts=attempts, finished_at=datetime.now(timezone.utc))
        emit_job_event_sync(
            redis_client,
            event_type='job_completed',
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=job_id,
            status='success',
            message='Job xử lý thành công',
            progress=100,
            trace_id=trace_id,
            attempts=attempts,
            filename=filename,
            result_url=f'/v1/jobs/{job_id}/result',
        )
        JOBS_COMPLETED_TOTAL.labels(tenant_id).inc()
        logger.info('Job completed successfully')

    except Exception as exc:
        error = str(exc)
        logger.exception('Job processing failed')
        next_attempt = attempts + 1

        if next_attempt <= settings.MAX_RETRY_ATTEMPTS:
            update_job_record_sync(job_id, status='retry_scheduled', progress=100, message=f'Lỗi tạm thời, sẽ retry lần {next_attempt}', error=error, attempts=next_attempt)
            emit_job_event_sync(
                redis_client,
                event_type='job_retry_scheduled',
                tenant_id=tenant_id,
                user_id=user_id,
                job_id=job_id,
                status='retry_scheduled',
                message=f'Sẽ retry lần {next_attempt}',
                progress=100,
                trace_id=trace_id,
                attempts=next_attempt,
                filename=filename,
                error=error,
            )
            job_payload['attempts'] = next_attempt
            schedule_retry(redis_client, job_payload, error)
            JOB_RETRIES_TOTAL.labels(tenant_id).inc()
        else:
            update_job_record_sync(job_id, status='failed_permanent', progress=100, message='Job thất bại vĩnh viễn và bị đưa vào DLQ', error=error, attempts=next_attempt, finished_at=datetime.now(timezone.utc))
            emit_job_event_sync(
                redis_client,
                event_type='job_failed_permanent',
                tenant_id=tenant_id,
                user_id=user_id,
                job_id=job_id,
                status='failed_permanent',
                message='Job bị đưa vào DLQ',
                progress=100,
                trace_id=trace_id,
                attempts=next_attempt,
                filename=filename,
                error=error,
            )
            send_to_dead_letter_queue(redis_client, {**job_payload, 'attempts': next_attempt}, error)
            JOB_DLQ_TOTAL.labels(tenant_id).inc()
            JOBS_FAILED_TOTAL.labels(tenant_id).inc()
    finally:
        JOB_PROCESSING_SECONDS.observe(time.perf_counter() - start)
        clear_trace_context()



def update_job_record_sync(job_id: str, **fields):
    """
    Worker là process sync, nên để sample ngắn gọn chúng ta dùng SQLAlchemy async qua hàm wrapper riêng.
    Trong production bạn có thể tách worker DB layer sang sync engine hoặc dùng task runner hỗ trợ async.
    """
    import asyncio
    asyncio.run(update_job_record(job_id, **fields))



def run_worker() -> None:
    import asyncio

    asyncio.run(init_db())
    start_http_server(settings.WORKER_METRICS_PORT)
    redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    logger.info('Worker started')
    logger.info('Worker metrics listening on :%s', settings.WORKER_METRICS_PORT)
    logger.info('Main queue=%s retry_zset=%s dlq=%s', settings.QUEUE_KEY, settings.RETRY_ZSET_KEY, settings.DLQ_KEY)

    while True:
        moved = move_due_retries_back_to_main_queue(redis_client)
        if moved:
            logger.info('Moved %s retry jobs back to main queue', moved)

        item = redis_client.brpop(settings.QUEUE_KEY, timeout=2)
        if not item:
            continue

        _, raw_payload = item
        try:
            job_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.error('Invalid JSON payload in queue: %s', raw_payload)
            continue

        process_one_job(redis_client, job_payload)


if __name__ == '__main__':
    run_worker()
