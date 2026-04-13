"""
---------
Worker chạy độc lập với FastAPI.

Luồng chạy:
1. BRPOP chờ job từ Redis queue
2. Cập nhật trạng thái job = processing
3. Xử lý file thật trong analyze_file
4. Lưu kết quả ra storage/results/<job_id>.json
5. Cập nhật trạng thái job = success hoặc failed
6. Ghi event vào Redis Stream để client nhận qua WebSocket

Vì sao worker tách khỏi FastAPI?
- tác vụ nặng không nên chặn request web
- có thể scale nhiều worker độc lập
- kiến trúc gần với production hơn
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import redis
import sys

# Mở comment 3 dòng bên dưới mỗi khi test (Chạy trực tiếp hàm if __main__)
import os,sys
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_DIR)

from app.config import settings
# from worker.processor import analyze_file

import hashlib


def analyze_file(upload_path: str) -> dict:
    """
    Đọc file upload và trả kết quả phân tích đơn giản.
    Hàm này thuần Python, không phụ thuộc FastAPI hay Redis.
    """
    path = Path(upload_path)
    raw = path.read_bytes()

    result = {
        "file_name": path.name,
        "file_size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }

    # Thử decode UTF-8. Nếu thành công thì xem như text.
    # Nếu thất bại, coi như binary.
    try:
        text = raw.decode("utf-8")
        result["file_kind"] = "text"
        result["line_count"] = len(text.splitlines())
        result["word_count"] = len(text.split())
        result["char_count"] = len(text)
        result["preview"] = text[:300]
    except UnicodeDecodeError:
        result["file_kind"] = "binary"
        result["preview_hex"] = raw[:32].hex(" ")

    return result


def utc_now_iso() -> str:
    """Thời gian UTC dạng ISO để ghi log và lưu trạng thái."""
    return datetime.now(timezone.utc).isoformat()


def job_key(job_id: str) -> str:
    """Sinh Redis key trạng thái job."""
    return f"{settings.JOB_KEY_PREFIX}{job_id}"


def emit_event(
    redis_client: redis.Redis,
    *,
    event_type: str,
    job_id: str,
    user_id: str,
    status: str,
    message: str,
    progress: int,
    filename: str = "",
    result_url: str = "",
    error: str = "",
) -> str:
    """
    Ghi event vào Redis Stream.

    FastAPI websocket sẽ đọc stream này để gửi tới client.
    """
    payload = {
        "type": event_type,
        "job_id": job_id,
        "user_id": user_id,
        "status": status,
        "message": message,
        "progress": str(progress),
        "result_url": result_url,
        "error": error,
        "filename": filename,
        "created_at": utc_now_iso(),
    }

    event_id = redis_client.xadd(
        settings.EVENT_STREAM_KEY,
        payload,
        maxlen=10_000,
        approximate=True,
    )
    return event_id


def update_job_state(redis_client: redis.Redis, job_id: str, **fields: str) -> None:
    """
    Ghi đè một phần trạng thái job vào Redis hash.
    Chỉ cập nhật các field truyền vào.
    """
    fields["updated_at"] = utc_now_iso()
    redis_client.hset(job_key(job_id), mapping=fields)


def process_one_job(redis_client: redis.Redis, job_payload: dict) -> None:
    """
    Xử lý duy nhất một job.
    Nếu có lỗi thì bắt exception ở đây để worker không chết toàn bộ vòng lặp.
    """
    job_id = job_payload["job_id"]
    user_id = job_payload["user_id"]
    filename = job_payload["filename"]
    upload_path = job_payload["upload_path"]

    try:
        # Bước 1: nhận job
        update_job_state(
            redis_client,
            job_id,
            status="processing",
            progress="10",
            message="Worker đã nhận job và bắt đầu xử lý",
            error="",
        )
        emit_event(
            redis_client,
            event_type="job_processing",
            job_id=job_id,
            user_id=user_id,
            status="processing",
            message="Worker đã nhận job",
            progress=10,
            filename=filename,
        )

        # time.sleep chỉ để demo nhìn rõ luồng event.
        # Production có thể bỏ đi.
        time.sleep(1)

        # Bước 2: đọc và phân tích file
        update_job_state(
            redis_client,
            job_id,
            progress="40",
            message="Đang đọc và phân tích nội dung file",
        )
        emit_event(
            redis_client,
            event_type="job_progress",
            job_id=job_id,
            user_id=user_id,
            status="processing",
            message="Đang phân tích file",
            progress=40,
            filename=filename,
        )

        analysis_result = analyze_file(upload_path)
        time.sleep(1)

        # Bước 3: chuẩn bị file kết quả
        update_job_state(
            redis_client,
            job_id,
            progress="80",
            message="Đã phân tích xong, đang ghi file kết quả",
        )
        emit_event(
            redis_client,
            event_type="job_progress",
            job_id=job_id,
            user_id=user_id,
            status="processing",
            message="Đang lưu kết quả",
            progress=80,
            filename=filename,
        )

        result_payload = {
            "job_id": job_id,
            "user_id": user_id,
            "source_file": filename,
            "processed_at": utc_now_iso(),
            "analysis": analysis_result,
        }

        result_path = settings.RESULT_DIR / f"{job_id}.json"
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        time.sleep(1)

        # Bước 4: đánh dấu thành công
        update_job_state(
            redis_client,
            job_id,
            status="success",
            progress="100",
            message="Job xử lý thành công",
            result_path=str(result_path),
            finished_at=utc_now_iso(),
            error="",
        )
        emit_event(
            redis_client,
            event_type="job_completed",
            job_id=job_id,
            user_id=user_id,
            status="success",
            message="Job xử lý thành công",
            progress=100,
            filename=filename,
            result_url=f"/v1/jobs/{job_id}/result",
        )

        print(f"[WORKER] Hoàn thành job {job_id}")

    except Exception as exc:
        update_job_state(
            redis_client,
            job_id,
            status="failed",
            progress="100",
            message="Job xử lý thất bại",
            error=str(exc),
            finished_at=utc_now_iso(),
        )
        emit_event(
            redis_client,
            event_type="job_failed",
            job_id=job_id,
            user_id=user_id,
            status="failed",
            message="Job xử lý thất bại",
            progress=100,
            filename=filename,
            error=str(exc),
        )
        print(f"[WORKER] Job {job_id} lỗi: {exc}")


def run_worker() -> None:
    """
    Vòng lặp vô hạn của worker.

    BRPOP sẽ block chờ đến khi queue có phần tử mới.
    Vì vậy worker không cần polling DB liên tục.
    """
    redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    print("[WORKER] Worker started")
    print(f"[WORKER] Redis URL: {settings.REDIS_URL}")
    print(f"[WORKER] Queue key: {settings.JOB_QUEUE_KEY}")

    while True:
        # BRPOP trả về tuple (queue_name, raw_payload)
        _, raw_payload = redis_client.brpop(settings.JOB_QUEUE_KEY, timeout=0)
        job_payload = json.loads(raw_payload)

        print(f"[WORKER] Nhận job: {job_payload['job_id']}")
        process_one_job(redis_client, job_payload)


if __name__ == "__main__":
    run_worker()
