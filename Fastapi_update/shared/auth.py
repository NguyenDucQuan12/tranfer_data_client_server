"""
-------
Authentication thật cho sample dựa trên JWT.

Để project dễ chạy, sample dùng user store in-memory.
Production nên thay bằng user table trong DB + password hash.

Điểm quan trọng ở đây là:
- API upload không tin user_id client gửi lên
- WebSocket cũng không tin path parameter tự khai
- cả hai đều lấy identity từ JWT đã ký
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, WebSocket, status

from shared.config import settings


@dataclass
class Identity:
    """
    Class dại diện cho danh tính đã xác thực. Bao gồm các thông tin bên dưới  
    Sau khi giải mã JWT thì hệ thống sẽ dùng class Identity
    """
    tenant_id: str
    user_id: str
    role: str
    trace_id: str | None = None


# User demo để chạy thử.
# password giữ plain-text chỉ để sample đơn giản.
# Khi làm thật, bạn nên lưu password hash trong DB.
DEMO_USERS = {
    'alice': {'password': 'alicepass', 'tenant_id': 'tenant_a', 'user_id': 'alice', 'role': 'user'},
    'admin_a': {'password': 'adminpass', 'tenant_id': 'tenant_a', 'user_id': 'admin_a', 'role': 'admin'},
    'bob': {'password': 'bobpass', 'tenant_id': 'tenant_b', 'user_id': 'bob', 'role': 'user'},
}



def authenticate_demo_user(username: str, password: str) -> dict | None:
    """
    Hàm kiểm tra user/password cho bộ dữ liệu demo
    """
    # Lấy user theo username
    user = DEMO_USERS.get(username)
    if not user:
        return None
    # So sánh password
    if not hmac.compare_digest(user['password'], password):
        return None
    return user



def create_access_token(*, tenant_id: str, user_id: str, role: str) -> tuple[str, int]:
    """
    Hàm tạo JWT
    """
    expire_delta = timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    expire_at = datetime.now(timezone.utc) + expire_delta
    payload = {
        'sub': user_id,
        'tenant_id': tenant_id,
        'user_id': user_id,
        'role': role,
        'exp': expire_at,
        'iat': datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, int(expire_delta.total_seconds())



def decode_access_token(token: str) -> Identity:
    """
    Giải mã JWT
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f'Invalid token: {exc}') from exc

    return Identity(
        tenant_id=payload['tenant_id'],
        user_id=payload['user_id'],
        role=payload.get('role', 'user'),
    )



def _extract_bearer_token(authorization: str | None) -> str:
    """
    Tách token ra khỏi header: Authorization: Bear <token>
    """
    # Nếu request không có header thì báo lõi
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing Authorization header')
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid Authorization header')
    return token


async def get_current_identity_from_request(authorization: Annotated[str | None, Header()] = None) -> Identity:
    """
    Dêpndecy dùng trong các router
    """
    token = _extract_bearer_token(authorization)
    return decode_access_token(token)



def decode_access_token_no_http(token: str) -> Identity:
    """
    Giống decode_access_token, nhưng lỗi được ném ra dưới dạng ValueError, không phải HTTPException.
    Tại sao cần:
    WebSocket không phải request HTTP thường, nên cách báo lỗi khác.
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise ValueError(f'Invalid token: {exc}') from exc
    return Identity(
        tenant_id=payload['tenant_id'],
        user_id=payload['user_id'],
        role=payload.get('role', 'user'),
    )


async def get_current_identity_from_websocket(websocket: WebSocket) -> Identity:
    """
    WebSocket không luôn tiện gửi header Authorization, nhất là ở browser.

    Vì vậy sample hỗ trợ 2 cách:
    1. Authorization header: Bearer <token>
    2. query param ?token=<jwt>

    Trong Python client, chúng ta dùng query param để đơn giản hóa demo.
    """
    auth_header = websocket.headers.get('authorization')
    token = None
    if auth_header:
        try:
            token = _extract_bearer_token(auth_header)
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc
    else:
        token = websocket.query_params.get('token')

    if not token:
        raise ValueError('Missing websocket token')

    return decode_access_token_no_http(token)
