from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class TokenRequest(BaseModel):
    """
    Input khi người dùng login
    """
    username: str
    password: str


class TokenResponse(BaseModel):
    """
    Output khi login  
    Khi người dùng đăng nhập thành công thì trả về các trường này
    """
    access_token: str
    token_type: str = 'bearer'
    expires_in_seconds: int
    tenant_id: str
    user_id: str
    role: str


class JobCreateResponse(BaseModel):
    """
    Các trường trả về khi tạo job
    """
    job_id: str
    status: str
    message: str
    status_url: str
    result_url: str
    websocket_url: str
    trace_id: str


class JobStatusResponse(BaseModel):
    """
    Các trường trả về khi kiểm tra trạng thái của job
    """
    job_id: str
    tenant_id: str
    user_id: str
    filename: str
    status: str
    progress: int
    message: str
    attempts: int
    upload_object_key: str
    result_object_key: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    trace_id: str
