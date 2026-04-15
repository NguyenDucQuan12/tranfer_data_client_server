"""
---------------------------
Service này chỉ chuyên xử lý WebSocket notification.

Vì sao tách riêng?
- upload API và WebSocket có profile tài nguyên khác nhau
- khi số kết nối realtime tăng mạnh, bạn có thể scale notification service riêng
- API service không phải giữ hàng nghìn socket mở
"""

from __future__ import annotations

import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import redis.asyncio as redis_async

from shared.auth import get_current_identity_from_websocket
from shared.config import settings
from shared.events import fields_to_event
from shared.logging_config import configure_logging, get_logger
from shared.metrics import WEBSOCKET_CONNECTIONS, WEBSOCKET_EVENTS_SENT_TOTAL, metrics_response
from shared.redis_keys import tenant_stream_key, user_stream_key
from shared.tracing import clear_trace_context, set_trace_context

configure_logging()
logger = get_logger('notification_service')

app = FastAPI(title='Notification service - websocket + replay')
redis_client = redis_async.from_url(settings.REDIS_URL, decode_responses=True)


async def replay_stream(websocket: WebSocket, *, stream_key: str, last_event_id: str, channel: str) -> str:
    """
    Hàm replay này giờ đọc trực tiếp từ stream riêng của user hoặc tenant.
    Vì vậy không cần if tenant/user để lọc từng event như bản cũ nữa.  
    Hàm này sẽ replay các envent cũ khi client disconnect
    """
    cursor = last_event_id
    while True:
        entries = await redis_client.xrange(stream_key, min=f'({cursor}', max='+', count=settings.REPLAY_BATCH_SIZE)
        if not entries:
            break
        for stream_id, fields in entries:
            cursor = stream_id
            await websocket.send_text(json.dumps(fields_to_event(stream_id, fields), ensure_ascii=False))
            WEBSOCKET_EVENTS_SENT_TOTAL.labels(channel).inc()
    return cursor


async def stream_live_events(websocket: WebSocket, *, stream_key: str, current_event_id: str, channel: str) -> None:
    """
    Hàm stream event liên tục
    """
    while True:
        # Gọi XREAD block trên stream kể từ current_event_id
        stream_response = await redis_client.xread({stream_key: current_event_id}, block=settings.STREAM_BLOCK_MS, count=10)
        if not stream_response:
            # Nếu chưa có event thì gửi {"type": "heartbeat"}
            await websocket.send_text(json.dumps({'type': 'heartbeat'}))
            continue
        # Nếu có event thì gửi từng event qua websocket
        for _, entries in stream_response:
            for stream_id, fields in entries:
                current_event_id = stream_id
                await websocket.send_text(json.dumps(fields_to_event(stream_id, fields), ensure_ascii=False))
                WEBSOCKET_EVENTS_SENT_TOTAL.labels(channel).inc()


@app.on_event('shutdown')
async def shutdown_event() -> None:
    await redis_client.close()


@app.get('/')
async def root() -> dict[str, str]:
    return {'message': 'Notification service is running'}


@app.get('/metrics')
async def metrics():
    return metrics_response()


@app.websocket('/ws/events')
async def websocket_user_events(websocket: WebSocket) -> None:
    """
    WebSocket riêng cho user app.

    Điểm thêm mới:
    - auth thật bằng JWT
    - stream riêng theo tenant/user
    - replay từ last_event_id để client tự reconnect không mất event
    """
    try:
        identity = await get_current_identity_from_websocket(websocket)
    except ValueError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return

    await websocket.accept()
    set_trace_context(tenant_id=identity.tenant_id, user_id=identity.user_id)
    stream_key = user_stream_key(identity.tenant_id, identity.user_id)
    last_event_id = websocket.query_params.get('last_event_id', '0-0')
    WEBSOCKET_CONNECTIONS.labels('user').inc()

    try:
        await websocket.send_text(json.dumps({'type': 'connected', 'stream_key': stream_key, 'last_event_id': last_event_id}))
        current_event_id = await replay_stream(websocket, stream_key=stream_key, last_event_id=last_event_id, channel='user')
        await stream_live_events(websocket, stream_key=stream_key, current_event_id=current_event_id, channel='user')
    except WebSocketDisconnect:
        logger.info('User websocket disconnected')
    finally:
        WEBSOCKET_CONNECTIONS.labels('user').dec()
        clear_trace_context()


@app.websocket('/ws/tenant-dashboard')
async def websocket_tenant_dashboard(websocket: WebSocket) -> None:
    """
    Kênh dashboard theo tenant.

    Chỉ user có role admin mới được nghe stream tenant-wide.
    """
    try:
        identity = await get_current_identity_from_websocket(websocket)
    except ValueError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return

    if identity.role != 'admin':
        await websocket.close(code=4403, reason='Admin role required')
        return

    await websocket.accept()
    set_trace_context(tenant_id=identity.tenant_id, user_id=identity.user_id)
    stream_key = tenant_stream_key(identity.tenant_id)
    last_event_id = websocket.query_params.get('last_event_id', '0-0')
    WEBSOCKET_CONNECTIONS.labels('tenant_dashboard').inc()

    try:
        await websocket.send_text(json.dumps({'type': 'connected', 'stream_key': stream_key, 'last_event_id': last_event_id}))
        current_event_id = await replay_stream(websocket, stream_key=stream_key, last_event_id=last_event_id, channel='tenant_dashboard')
        await stream_live_events(websocket, stream_key=stream_key, current_event_id=current_event_id, channel='tenant_dashboard')
    except WebSocketDisconnect:
        logger.info('Dashboard websocket disconnected')
    finally:
        WEBSOCKET_CONNECTIONS.labels('tenant_dashboard').dec()
        clear_trace_context()
