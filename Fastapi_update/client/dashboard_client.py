from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import websockets

from client.common import get_token
from shared.config import settings

STATE_FILE = Path(__file__).resolve().parent / 'dashboard_state.json'



def load_state() -> dict:
    if not STATE_FILE.exists():
        return {'last_event_id': '0-0'}
    return json.loads(STATE_FILE.read_text(encoding='utf-8'))



def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


async def run_dashboard(username: str, password: str) -> None:
    state = load_state()
    token = get_token(username, password)['access_token']
    reconnect_delay = 1

    while True:
        try:
            ws_url = f"{settings.NOTIFICATION_BASE_URL}/ws/tenant-dashboard?token={token}&last_event_id={state['last_event_id']}"
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as websocket:
                reconnect_delay = 1
                async for raw in websocket:
                    message = json.loads(raw)
                    event_id = message.get('event_id')
                    if event_id and event_id > state.get('last_event_id', '0-0'):
                        state['last_event_id'] = event_id
                        save_state(state)
                    print(json.dumps(message, ensure_ascii=False, indent=2))
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f'[DASHBOARD] {exc}')
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', required=True)
    parser.add_argument('--password', required=True)
    args = parser.parse_args()
    asyncio.run(run_dashboard(args.username, args.password))
