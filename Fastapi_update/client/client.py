"""
client.py
---------
Python client tự reconnect và luôn nhận event mới nhất của chính user.

Điểm mới so với bản cũ:
- phải login để lấy JWT trước
- WebSocket gửi token để auth thật
- stream giờ là stream riêng của user nên không cần lọc event user khác
- vẫn lưu last_event_id để reconnect và replay đúng phần bị lỡ
"""

from __future__ import annotations

import argparse # dùng để đọc tham số dòng lệnh khi chạy file: python client.py --username alice --password 123456
import asyncio
import json
from pathlib import Path

import websockets

from client.common import get_token
from shared.config import settings

# Lấy thư mục cha làm đường dẫn
# Nếu client.py nằm ở: D:/project/client/client.py
# thì STATE_FILE sẽ là: D:/project/client/client_state.json
STATE_FILE = Path(__file__).resolve().parent / 'client_state.json'



def load_state() -> dict:
    """
    Lấy các giá trị cũ từ file nếu có
    """
    if not STATE_FILE.exists():
        return {'last_event_id': '0-0'}
    return json.loads(STATE_FILE.read_text(encoding='utf-8'))



def save_state(state: dict) -> None:
    # Lưu trạng thái hiện tại xuống file
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


async def handle_event(message: dict, state: dict) -> None:
    """
    Xử lý mỗi message nhận được tư websocket
    """
    # Lấy thông tin từ event
    event_type = message.get('type')
    event_id = message.get('event_id')

    # Kiểm tra nếu id event hiện tại lớn hơn event cũ thì mới ghi lại, không thì tức là event cũ, bỏ qua
    if event_id and event_id > state.get('last_event_id', '0-0'):
        state['last_event_id'] = event_id
        save_state(state)

    print(json.dumps(message, ensure_ascii=False, indent=2))


async def run_client(username: str, password: str) -> None:
    """
    Chạy hàm 
    """
    # Đọc trạng thái cũ từ tệp client_state.json
    state = load_state()
    token_data = get_token(username, password)
    token = token_data['access_token']
    reconnect_delay = 1

    while True:
        try:
            ws_url = f"{settings.NOTIFICATION_BASE_URL}/ws/events?token={token}&last_event_id={state['last_event_id']}"
            print(f'[CLIENT] Connecting to {ws_url}')
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as websocket:
                reconnect_delay = 1
                async for raw_message in websocket:
                    message = json.loads(raw_message)
                    await handle_event(message, state)
        except KeyboardInterrupt:
            print('[CLIENT] Stop by user')
            return
        except Exception as exc:
            print(f'[CLIENT] Connection error: {exc}')
            print(f'[CLIENT] Reconnect after {reconnect_delay} seconds')
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', required=True)
    parser.add_argument('--password', required=True)
    args = parser.parse_args()
    asyncio.run(run_client(args.username, args.password))
