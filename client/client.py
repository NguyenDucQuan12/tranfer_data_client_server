"""
client.py
---------
Python client tự reconnect và luôn cố gắng nhận event mới nhất.

Chiến lược:
1. Client nhớ event_id cuối cùng đã xử lý vào file state.
2. Khi mở lại app / mất mạng rồi reconnect:
   - client gửi last_event_id lên server qua query string
   - server replay lại các event sau event_id đó
3. Client deduplicate bằng cách chỉ nhận event mới hơn state hiện tại.

Đây là ý tưởng quan trọng nhất của bài toán:
- đừng chỉ cố giữ socket sống mãi
- hãy thiết kế để nếu socket chết, client vẫn lấy lại được phần bị lỡ
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import websockets

# ---------------------------
# Cấu hình cơ bản cho client
# ---------------------------
SERVER_WS_BASE = "ws://127.0.0.1:8000/ws/users"
USER_ID = "user_demo_01"

# File state giúp client nhớ event_id cuối cùng ngay cả khi app bị tắt.
STATE_FILE = Path(__file__).resolve().parent / "client_state.json"


def load_state() -> dict[str, Any]:
    """
    Đọc state đã lưu trước đó.
    Nếu file chưa tồn tại thì trả state mặc định.
    """
    if not STATE_FILE.exists():
        return {"last_event_id": "0-0"}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        # Nếu file state bị hỏng, fallback về mốc đầu tiên.
        return {"last_event_id": "0-0"}


def save_state(state: dict[str, Any]) -> None:
    """Ghi state ra file JSON."""
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_stream_id(event_id: str) -> tuple[int, int]:
    """
    Redis Stream ID có dạng: "<milliseconds>-<sequence>"
    Ví dụ: "1713000000000-0"

    So sánh chuỗi trực tiếp không thật sự an toàn,
    nên ta tách thành 2 số nguyên để so sánh đúng thứ tự.
    """
    major, minor = event_id.split("-")
    return int(major), int(minor)


def is_newer_event(new_event_id: str, current_event_id: str) -> bool:
    """Trả True nếu new_event_id mới hơn current_event_id."""
    return parse_stream_id(new_event_id) > parse_stream_id(current_event_id)


async def handle_event(message: dict[str, Any], state: dict[str, Any]) -> None:
    """
    Xử lý một bản tin từ server.

    Server có thể gửi:
    - connected
    - heartbeat
    - job_queued
    - job_processing
    - job_progress
    - job_completed
    - job_failed
    - server_error
    """
    message_type = message.get("type")

    if message_type == "connected":
        print(f"[CLIENT] Đã kết nối WebSocket cho user_id={message.get('user_id')}")
        print(f"[CLIENT] Server time: {message.get('server_time')}")
        print(f"[CLIENT] Replay sẽ bắt đầu sau event_id={message.get('last_event_id')}")
        return

    if message_type == "heartbeat":
        print(f"[CLIENT] Heartbeat from server at {message.get('server_time')}")
        return

    if message_type == "server_error":
        print(f"[CLIENT] Server error: {message.get('message')}")
        return

    event_id = message.get("event_id")
    if not event_id:
        print(f"[CLIENT] Bản tin không có event_id: {message}")
        return

    # Chống xử lý trùng trong trường hợp reconnect hoặc gửi lại event cũ.
    if not is_newer_event(event_id, state["last_event_id"]):
        print(f"[CLIENT] Bỏ qua event cũ hoặc trùng: {event_id}")
        return

    # Chỉ khi chắc chắn là event mới, ta mới cập nhật state.
    state["last_event_id"] = event_id
    save_state(state)

    job_id = message.get("job_id")
    status = message.get("status")
    progress = message.get("progress")
    info = message.get("message")

    print("=" * 80)
    print(f"[CLIENT] EVENT MỚI  : {event_id}")
    print(f"[CLIENT] JOB ID     : {job_id}")
    print(f"[CLIENT] TYPE       : {message_type}")
    print(f"[CLIENT] STATUS     : {status}")
    print(f"[CLIENT] PROGRESS   : {progress}")
    print(f"[CLIENT] MESSAGE    : {info}")
    print(f"[CLIENT] CREATED AT : {message.get('created_at')}")

    # Xử lý riêng cho từng loại event để bạn dễ mở rộng sau này.
    if message_type == "job_completed":
        print(f"[CLIENT] RESULT URL : {message.get('result_url')}")
        print("[CLIENT] >>> Job đã hoàn thành thành công")

    elif message_type == "job_failed":
        print(f"[CLIENT] ERROR      : {message.get('error')}")
        print("[CLIENT] >>> Job đã thất bại")

    print("=" * 80)


async def run_client() -> None:
    """
    Vòng lặp kết nối vô hạn:
    - kết nối websocket
    - nếu đứt thì chờ một lúc rồi reconnect
    - reconnect theo exponential backoff
    """
    state = load_state()
    reconnect_delay = 1

    while True:
        last_event_id = state["last_event_id"]
        ws_url = f"{SERVER_WS_BASE}/{USER_ID}?last_event_id={last_event_id}"

        try:
            print(f"[CLIENT] Đang kết nối tới: {ws_url}")

            # ping_interval / ping_timeout giúp phát hiện socket chết sớm hơn.
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
            ) as websocket:
                print("[CLIENT] Kết nối thành công")
                reconnect_delay = 1

                async for raw_message in websocket:
                    try:
                        message = json.loads(raw_message)
                    except json.JSONDecodeError:
                        print(f"[CLIENT] Không parse được JSON: {raw_message}")
                        continue

                    await handle_event(message, state)

        except KeyboardInterrupt:
            print("[CLIENT] Người dùng dừng chương trình")
            return

        except Exception as exc:
            print(f"[CLIENT] Mất kết nối hoặc lỗi: {exc}")
            print(f"[CLIENT] Sẽ thử reconnect sau {reconnect_delay} giây...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 30)


if __name__ == "__main__":
    asyncio.run(run_client())
