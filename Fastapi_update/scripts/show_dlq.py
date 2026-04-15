from __future__ import annotations

import json

import redis

from shared.config import settings


r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
items = r.lrange(settings.DLQ_KEY, 0, -1)
print(json.dumps([json.loads(item) for item in items], ensure_ascii=False, indent=2))
