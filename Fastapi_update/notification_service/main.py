"""
---------------------------
Service này chỉ chuyên xử lý WebSocket notification.

Vì sao tách riêng?
- upload API và WebSocket có profile tài nguyên khác nhau
- khi số kết nối realtime tăng mạnh, bạn có thể scale notification service riêng
- API service không phải giữ hàng nghìn socket mở

client kết nối WebSocket
xác thực user bằng JWT
đọc event cũ từ Redis Stream để replay
nghe event mới liên tục từ Redis Stream để đẩy realtime
ghi metrics và logging
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

# Tạo Redis client async kết nối tới Redis. Redis sẽ giải mã và trả về chuỗi text thay vì bytes
redis_client = redis_async.from_url(settings.REDIS_URL, decode_responses=True)


async def replay_stream(websocket: WebSocket, *, stream_key: str, last_event_id: str, channel: str) -> str:
    """
    Hàm này gửi lại các event cũ mà client đã bỏ lỡ  
    Ví dụ: client hiện đã nhận đến event 100  
    Sau đó mất mạng (khong thể kết nối tới server)  
    Server vẫn tiếp tục phát các event 101, 102, 103  
    Client kết nối lại và báo id của event nhận cuối cùng là 100  
    Server sẽ gửi lại các event sau event 100 đến event mới nhất cho người dùng  
    """
    # Lấy id của event cuối cùng mà người dùng nhận được
    cursor = last_event_id
    # Lặp liên tục theo từng batch để đọc event cũ
    while True:
        # Đọc các event từ Redis Stream trong một khoảng ID
        # min=f'({cursor}': lấy các event lớn hơn cursor, không lấy lại chính cursor, max='+': đọc tới event mới nhất  
        # min='100-0' nghĩa là lấy cả event 100-0
        # min='(100-0' nghĩa là lấy event sau 100-0
        # count=settings.REPLAY_BATCH_SIZE: chỉ đọc tối đa một batch
        entries = await redis_client.xrange(stream_key, min=f'({cursor}', max='+', count=settings.REPLAY_BATCH_SIZE)

        # Nếu không có event nào bỏ lỡ thì thoát hàm
        if not entries:
            break

        # Đọc các thông tin từ event Redis đọc được
        for stream_id, fields in entries:
            # Cập nhật id event để lần tiếp theo sẽ đọc lại dữ liệu từ event này tới event mới nhất
            cursor = stream_id
            # Gửi chuỗi event tới client thông qua websocket
            # Trong đó fields_to_event(stream_id, fields) là chuyển dữ liệu Redis Stream thô thành event chuẩn hóa hơn  
            # json.dumps(..., ensure_ascii=False) sẽ tiến hành đổi object Python thành Json text  
            await websocket.send_text(json.dumps(fields_to_event(stream_id, fields), ensure_ascii=False))

            # Tăng thêm 1 đơn vị cho số lượng event đã gửi qua token
            WEBSOCKET_EVENTS_SENT_TOTAL.labels(channel).inc()
    return cursor


async def stream_live_events(websocket: WebSocket, *, stream_key: str, current_event_id: str, channel: str) -> None:
    """
    hàm này sẽ ngồi chờ event mới liên tục và gửi realtime cho client.
    """
    # Lặp vô hạn để giữa websocket sống và nghe event mới
    while True:
        # Gọi XREAD block trên stream kể từ current_event_id để đọc các event mới
        stream_response = await redis_client.xread({stream_key: current_event_id}, block=settings.STREAM_BLOCK_MS, count=10)

        if not stream_response:
            # Nếu chưa có event thì gửi {"type": "heartbeat"}
            await websocket.send_text(json.dumps({'type': 'heartbeat'}))
            continue

        # Nếu có event thì gửi từng event qua websocket và tăng số lượng đếm event đã gửi qua socket
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
    return {'message': 'Máy chủ thông báo đang hoạt động'}


@app.get('/metrics')
async def metrics():
    # Trả về metrics Prometheus của máy chủ thông báo
    return metrics_response()


@app.websocket('/ws/events')
async def websocket_user_events(websocket: WebSocket) -> None:
    """
    WebSocket riêng cho một user app.  
    Xác thực thông tin người dùng bằng JWT  
    Replay các event cũ nếu user bị lỡ  
    Stream event mới liên tục  
    """
    try:
        # Tiến hành xác thực người dùng từ websocket
        identity = await get_current_identity_from_websocket(websocket)
    except ValueError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return

    # Chấp nhận kết nối websocket
    await websocket.accept()
    # Gắn context logging cho kết nối hiện tại
    set_trace_context(tenant_id=identity.tenant_id, user_id=identity.user_id)
    # Tạo tên stream riêng cho user này
    stream_key = user_stream_key(identity.tenant_id, identity.user_id)
    # Lấy last event từ queru string hoặc nếu ko có thì gán là 0-0
    last_event_id = websocket.query_params.get('last_event_id', '0-0')
    # Tăng bộ đếm số lượng user đang kết nối tới Socket0.
    WEBSOCKET_CONNECTIONS.labels('user').inc()

    try:
        # Gửi ngay 1 event xác nhận đã kết nối tới client
        await websocket.send_text(json.dumps({'type': 'connected', 'stream_key': stream_key, 'last_event_id': last_event_id}))
        # Gọi hàm lấy các event cũ (nếu user bị miss event thì hàm này lấy các event bị mất đó cho người dùng)
        current_event_id = await replay_stream(websocket, stream_key=stream_key, last_event_id=last_event_id, channel='user')
        # Sau khi Replay xong thì lắng nghe các event liên tục (live)
        await stream_live_events(websocket, stream_key=stream_key, current_event_id=current_event_id, channel='user')

    except WebSocketDisconnect:
        # Nếu người dùng mất kết nối thì ghi log lại
        logger.info(f'Mất kết nối tới người dùng {identity.user_id}')
    finally:
        # Tiến hành giảm số lượng người dùng đang kết nối tới Websocket
        WEBSOCKET_CONNECTIONS.labels('user').dec()
        # Xóa các contetx liên quan để tránh rò rỉ
        clear_trace_context()


@app.websocket('/ws/tenant-dashboard')
async def websocket_tenant_dashboard(websocket: WebSocket) -> None:
    """
    Kênh dashboard theo tenant.

    Chỉ user có role admin mới được nghe stream tenant-wide.
    """
    try:
        # Xác thực thông tin người kết nối tới websocket
        identity = await get_current_identity_from_websocket(websocket)
    except ValueError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return

    # Xác thực quyền của người dùng có phải là admin không
    if identity.role != 'admin':
        await websocket.close(code=4403, reason='Không phải là Admin')
        return

    # Chấp nhận kết nối và truyền các thông tin
    await websocket.accept()
    set_trace_context(tenant_id=identity.tenant_id, user_id=identity.user_id)
    stream_key = tenant_stream_key(identity.tenant_id)
    last_event_id = websocket.query_params.get('last_event_id', '0-0')
    # Tăng số lượng socket đã kết nối tới 
    WEBSOCKET_CONNECTIONS.labels('tenant_dashboard').inc()

    try:
        # Gửi 1 event xác nhận kết nối tới người dùng
        await websocket.send_text(json.dumps({'type': 'connected', 'stream_key': stream_key, 'last_event_id': last_event_id}))
        current_event_id = await replay_stream(websocket, stream_key=stream_key, last_event_id=last_event_id, channel='tenant_dashboard')
        await stream_live_events(websocket, stream_key=stream_key, current_event_id=current_event_id, channel='tenant_dashboard')
    except WebSocketDisconnect:
        logger.info('Dashboard websocket disconnected')
    finally:
        WEBSOCKET_CONNECTIONS.labels('tenant_dashboard').dec()
        clear_trace_context()
