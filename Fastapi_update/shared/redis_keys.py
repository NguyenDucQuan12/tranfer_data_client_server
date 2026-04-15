"""
-------------
Tập trung logic tạo key Redis.

Điểm mới:
- stream không còn dùng một key chung cho cả hệ thống
- có stream riêng theo user và theo tenant/dashboard
"""

from __future__ import annotations

from shared.config import settings


def user_stream_key(tenant_id: str, user_id: str) -> str:
    """
    Tạo key stream riêng cho từng user  
    Mỗi user sẽ có một key riêng, tránh đọc stream chồng chéo nhau
    """
    return f"{settings.STREAM_PREFIX}:tenant:{tenant_id}:user:{user_id}"


def tenant_stream_key(tenant_id: str) -> str:
    """
    Tạo kêy stream dashboard của tenant
    """
    return f"{settings.STREAM_PREFIX}:tenant:{tenant_id}:dashboard"


def job_status_key(job_id: str) -> str:
    """
    Trả key trạng thái job
    """
    return f"{settings.JOB_STATUS_KEY_PREFIX}:{job_id}"
