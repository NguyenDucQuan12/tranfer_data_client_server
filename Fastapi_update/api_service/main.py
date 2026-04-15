"""
-----------
Service này chỉ tập trung vào:
- login lấy JWT
- upload file
- đọc trạng thái job
- tải kết quả

WebSocket được tách sang notification_service/main.py để scale độc lập.
"""

from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
import redis.asyncio as redis_async

from shared.auth import Identity, authenticate_demo_user, create_access_token, get_current_identity_from_request
from shared.config import settings
from shared.db import create_job_record, get_job_record, init_db
from shared.events import emit_job_event
from shared.logging_config import configure_logging, get_logger
from shared.metrics import JOBS_CREATED_TOTAL, UPLOAD_BYTES_TOTAL, UPLOAD_REJECTED_TOTAL, UPLOAD_REQUESTS_TOTAL, metrics_response
from shared.schemas import JobCreateResponse, JobStatusResponse, TokenRequest, TokenResponse
from shared.storage import get_storage_backend
from shared.tracing import clear_trace_context, new_trace_id, set_trace_context

configure_logging()
logger = get_logger('api_service')

app = FastAPI(title='API service - upload + auth + status')
redis_client = redis_async.from_url(settings.REDIS_URL, decode_responses=True)
storage = get_storage_backend()


@app.middleware('http')
async def request_context_middleware(request: Request, call_next):
    # Lấy x-request-id từ header nếu client gửi sẵn. Nếu không có thì sinh mới.
    trace_id = request.headers.get('x-request-id') or new_trace_id()
    # Gán các giá trị vào context
    set_trace_context(trace_id=trace_id)

    # Chạy request và gán x-request-id vào response
    try:
        response = await call_next(request)
        response.headers['x-request-id'] = trace_id
        return response
    finally:
        # Clear context
        clear_trace_context()


@app.on_event('startup')
async def startup_event() -> None:
    await init_db()
    logger.info('API service started')


@app.on_event('shutdown')
async def shutdown_event() -> None:
    await redis_client.close()


@app.get('/')
async def root() -> dict[str, str]:
    return {'message': 'API service is running'}


@app.get('/metrics')
async def metrics():
    return metrics_response()


@app.post('/v1/auth/token', response_model=TokenResponse)
async def issue_token(payload: TokenRequest) -> TokenResponse:
    """
    Hàm này là nơi cấp JWT cho client.

    Nó thay thế cách cũ là client tự gửi user_id vào upload API.
    Sau khi login thành công, mọi request tiếp theo sẽ mang Bearer token.
    """
    user = authenticate_demo_user(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail='Invalid username or password')

    token, expires_in = create_access_token(
        tenant_id=user['tenant_id'],
        user_id=user['user_id'],
        role=user['role'],
    )
    return TokenResponse(
        access_token=token,
        expires_in_seconds=expires_in,
        tenant_id=user['tenant_id'],
        user_id=user['user_id'],
        role=user['role'],
    )


@app.post('/v1/jobs/upload', response_model=JobCreateResponse)
async def upload_job(
    request: Request,
    file: UploadFile = File(...),
    identity: Identity = Depends(get_current_identity_from_request),
) -> JobCreateResponse:
    """
    Upload API đã được nâng cấp ở 3 điểm quan trọng:

    1. Xác thực bằng JWT thật qua dependency `get_current_identity_from_request`
    2. Giới hạn dung lượng file ngay tại storage backend
    3. Ghi metadata bền vững xuống PostgreSQL trước khi enqueue
    """
    trace_id = request.headers.get('x-request-id') or new_trace_id()
    set_trace_context(trace_id=trace_id, tenant_id=identity.tenant_id, user_id=identity.user_id)

    UPLOAD_REQUESTS_TOTAL.labels(identity.tenant_id).inc()

    safe_filename = Path(file.filename or 'unnamed.bin').name
    job_id = uuid.uuid4().hex
    object_key = f"tenant/{identity.tenant_id}/user/{identity.user_id}/uploads/{job_id}_{safe_filename}"

    try:
        stored = await storage.save_upload_file(
            upload_file=file,
            object_key=object_key,
            max_bytes=settings.MAX_UPLOAD_BYTES,
        )
    except HTTPException as exc:
        UPLOAD_REJECTED_TOTAL.labels('payload_too_large').inc()
        raise exc
    finally:
        await file.close()

    UPLOAD_BYTES_TOTAL.labels(identity.tenant_id).inc(stored.size_bytes)

    await create_job_record(
        job_id=job_id,
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        filename=safe_filename,
        upload_object_key=stored.object_key,
        trace_id=trace_id,
    )

    queue_payload = {
        'job_id': job_id,
        'tenant_id': identity.tenant_id,
        'user_id': identity.user_id,
        'filename': safe_filename,
        'upload_object_key': stored.object_key,
        'attempts': 0,
        'trace_id': trace_id,
    }
    await redis_client.lpush(settings.QUEUE_KEY, json.dumps(queue_payload, ensure_ascii=False))

    await emit_job_event(
        redis_client,
        event_type='job_queued',
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        job_id=job_id,
        status='queued',
        message='Server đã nhận file và đưa job vào queue',
        progress=0,
        trace_id=trace_id,
        attempts=0,
        filename=safe_filename,
    )

    JOBS_CREATED_TOTAL.labels(identity.tenant_id).inc()
    logger.info('Created job and queued it')

    return JobCreateResponse(
        job_id=job_id,
        status='queued',
        message='Upload thành công, job đã vào queue',
        status_url=f'/v1/jobs/{job_id}',
        result_url=f'/v1/jobs/{job_id}/result',
        websocket_url=f"{settings.NOTIFICATION_BASE_URL}/ws/events",
        trace_id=trace_id,
    )


@app.get('/v1/jobs/{job_id}', response_model=JobStatusResponse)
async def get_job_status(job_id: str, identity: Identity = Depends(get_current_identity_from_request)) -> JobStatusResponse:
    record = await get_job_record(job_id)
    if not record:
        raise HTTPException(status_code=404, detail='Job not found')

    # User thường chỉ xem job của chính mình. Admin tenant có thể xem mọi job trong tenant.
    if record.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=403, detail='Cross-tenant access denied')
    if identity.role != 'admin' and record.user_id != identity.user_id:
        raise HTTPException(status_code=403, detail='Not allowed to view this job')

    return JobStatusResponse(
        job_id=record.job_id,
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        filename=record.filename,
        status=record.status,
        progress=record.progress,
        message=record.message,
        attempts=record.attempts,
        upload_object_key=record.upload_object_key,
        result_object_key=record.result_object_key,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finished_at=record.finished_at,
        trace_id=record.trace_id,
    )


@app.get('/v1/jobs/{job_id}/result')
async def get_job_result(job_id: str, identity: Identity = Depends(get_current_identity_from_request)):
    record = await get_job_record(job_id)
    if not record:
        raise HTTPException(status_code=404, detail='Job not found')
    if record.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=403, detail='Cross-tenant access denied')
    if identity.role != 'admin' and record.user_id != identity.user_id:
        raise HTTPException(status_code=403, detail='Not allowed to access this result')
    if record.status != 'success' or not record.result_object_key:
        raise HTTPException(status_code=409, detail='Result is not ready')

    if settings.OBJECT_STORAGE_BACKEND.lower() == 'local':
        result_file = storage.resolve_local_path(record.result_object_key)
        media_type = mimetypes.guess_type(result_file.name)[0] or 'application/json'
        return FileResponse(result_file, media_type=media_type, filename=result_file.name)

    # Với S3 backend, sample trả bytes trực tiếp.
    data = storage.read_bytes(record.result_object_key)
    return Response(content=data, media_type='application/json', headers={'content-disposition': f'attachment; filename={job_id}.json'})
