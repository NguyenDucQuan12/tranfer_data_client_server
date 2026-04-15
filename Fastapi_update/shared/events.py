"""
---------
Logic ghi và đọc Redis Stream.

Điểm mới:
- mỗi event được ghi vào 2 stream:
  1. stream riêng của user để Python client / app client đọc
  2. stream tenant/dashboard để admin dashboard đọc
- retention policy dùng MAXLEN, cấu hình riêng cho từng loại stream
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis_async
import redis

from shared.config import settings
from shared.redis_keys import tenant_stream_key, user_stream_key



def utc_now_iso() -> str:
    """
    Định dạng thời gian với hiện tại
    """
    return datetime.now(timezone.utc).isoformat()



def _event_payload(*, event_type: str, tenant_id: str, user_id: str, job_id: str, status: str, message: str, progress: int, trace_id: str, attempts: int, filename: str = '', result_url: str = '', error: str = '') -> dict[str, str]:
    """
    Tạo cấu trúc payload thống nhất  
    Mọi event phát ra đều có đầy đủ các thông tin này
    """
    return {
        'type': event_type,
        'tenant_id': tenant_id,
        'user_id': user_id,
        'job_id': job_id,
        'status': status,
        'message': message,
        'progress': str(progress),
        'trace_id': trace_id,
        'attempts': str(attempts),
        'filename': filename,
        'result_url': result_url,
        'error': error,
        'created_at': utc_now_iso(),
    }


async def emit_job_event(redis_client: redis_async.Redis, *, event_type: str, tenant_id: str, user_id: str, job_id: str, status: str, message: str, progress: int, trace_id: str, attempts: int, filename: str = '', result_url: str = '', error: str = '') -> tuple[str, str]:
    """
    Phiên bản bất đồng bộ để ghi event  
    1. Tạo payload thông qua _event_payload
    2. Ghi vào user stream bằng XADD 
    3. Ghi vào tenant stream bằng XADD
    4. Mỗi stream đều áp dụng MAXLEN
    5. Trả về (user_stream_id, tenant_stream_id)
    """
    payload = _event_payload(
        event_type=event_type,
        tenant_id=tenant_id,
        user_id=user_id,
        job_id=job_id,
        status=status,
        message=message,
        progress=progress,
        trace_id=trace_id,
        attempts=attempts,
        filename=filename,
        result_url=result_url,
        error=error,
    )

    # User stream: client app của một user chỉ đọc đúng stream của user đó.
    # Đây là thay đổi chính để giảm việc đọc stream chung rồi lọc event.
    user_stream_id = await redis_client.xadd(
        user_stream_key(tenant_id, user_id),
        payload,
        maxlen=settings.STREAM_MAXLEN_USER,
        approximate=True,
    )

    # Tenant stream: dashboard của admin tenant đọc toàn bộ event tenant.
    tenant_stream_id = await redis_client.xadd(
        tenant_stream_key(tenant_id),
        payload,
        maxlen=settings.STREAM_MAXLEN_TENANT,
        approximate=True,
    )
    return user_stream_id, tenant_stream_id



def emit_job_event_sync(redis_client: redis.Redis, **kwargs: Any) -> tuple[str, str]:
    """
    Giống emit_job_event, nhưng dùng Redis sync client.
    """
    payload = _event_payload(**kwargs)
    user_stream_id = redis_client.xadd(
        user_stream_key(kwargs['tenant_id'], kwargs['user_id']),
        payload,
        maxlen=settings.STREAM_MAXLEN_USER,
        approximate=True,
    )
    tenant_stream_id = redis_client.xadd(
        tenant_stream_key(kwargs['tenant_id']),
        payload,
        maxlen=settings.STREAM_MAXLEN_TENANT,
        approximate=True,
    )
    return user_stream_id, tenant_stream_id



def fields_to_event(stream_id: str, fields: dict[str, str]) -> dict[str, Any]:
    """
    Chuyển raw Redis Stream record thành dict chuẩn để gửi ra WebSocket.Chuyển raw Redis Stream record thành dict chuẩn để gửi ra WebSocket.
    """
    return {
        'event_id': stream_id,
        'type': fields.get('type', ''),
        'tenant_id': fields.get('tenant_id', ''),
        'user_id': fields.get('user_id', ''),
        'job_id': fields.get('job_id', ''),
        'status': fields.get('status', ''),
        'message': fields.get('message', ''),
        'progress': int(fields.get('progress', '0')),
        'trace_id': fields.get('trace_id', ''),
        'attempts': int(fields.get('attempts', '0')),
        'filename': fields.get('filename', ''),
        'result_url': fields.get('result_url', ''),
        'error': fields.get('error', ''),
        'created_at': fields.get('created_at', ''),
    }
