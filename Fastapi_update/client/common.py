from __future__ import annotations

import requests

from shared.config import settings



def get_token(username: str, password: str) -> dict:
    response = requests.post(
        f'{settings.API_BASE_URL}/v1/auth/token',
        json={'username': username, 'password': password},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
