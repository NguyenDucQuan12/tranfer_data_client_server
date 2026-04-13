"""
schemas.py
----------
Các model trả về từ API.
Dùng Pydantic giúp dữ liệu trả ra có cấu trúc rõ ràng hơn.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JobCreateResponse(BaseModel):
    job_id: str
    user_id: str
    status: str
    message: str
    websocket_url: str
    status_url: str
    result_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    user_id: str
    filename: str
    status: str
    progress: int
    message: str
    upload_path: str
    result_path: Optional[str] = None
    error: Optional[str] = None
    uploaded_at: str
    updated_at: str
    finished_at: Optional[str] = None
