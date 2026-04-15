"""
Đọc các thông tin từ biến môi trường và để mọi service dùng chung
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


class Settings:
    BASE_DIR = BASE_DIR

    REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql+asyncpg://demo_user:demo_pass@127.0.0.1:5432/realtime_demo')

    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'change-me-in-production')
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRE_MINUTES = int(os.getenv('JWT_EXPIRE_MINUTES', '120'))

    API_BASE_URL = os.getenv('API_BASE_URL', 'http://127.0.0.1:8000')
    NOTIFICATION_BASE_URL = os.getenv('NOTIFICATION_BASE_URL', 'ws://127.0.0.1:8001')

    QUEUE_KEY = os.getenv('QUEUE_KEY', 'prod_demo:queue:main')
    RETRY_ZSET_KEY = os.getenv('RETRY_ZSET_KEY', 'prod_demo:queue:retry')
    DLQ_KEY = os.getenv('DLQ_KEY', 'prod_demo:queue:dlq')

    STREAM_PREFIX = os.getenv('STREAM_PREFIX', 'prod_demo:stream')
    JOB_STATUS_KEY_PREFIX = os.getenv('JOB_STATUS_KEY_PREFIX', 'prod_demo:job')
    JOB_STATUS_TTL_SECONDS = int(os.getenv('JOB_STATUS_TTL_SECONDS', '604800'))

    STREAM_MAXLEN_USER = int(os.getenv('STREAM_MAXLEN_USER', '5000'))
    STREAM_MAXLEN_TENANT = int(os.getenv('STREAM_MAXLEN_TENANT', '10000'))
    STREAM_BLOCK_MS = int(os.getenv('STREAM_BLOCK_MS', '15000'))
    REPLAY_BATCH_SIZE = int(os.getenv('REPLAY_BATCH_SIZE', '100'))

    MAX_UPLOAD_BYTES = int(os.getenv('MAX_UPLOAD_BYTES', str(10 * 1024 * 1024)))

    MAX_RETRY_ATTEMPTS = int(os.getenv('MAX_RETRY_ATTEMPTS', '3'))
    RETRY_BASE_SECONDS = int(os.getenv('RETRY_BASE_SECONDS', '5'))

    OBJECT_STORAGE_BACKEND = os.getenv('OBJECT_STORAGE_BACKEND', 'local')
    WORKER_METRICS_PORT = int(os.getenv('WORKER_METRICS_PORT', '8010'))
    LOCAL_OBJECT_STORAGE_DIR = Path(os.getenv('LOCAL_OBJECT_STORAGE_DIR', str(BASE_DIR / 'storage' / 'local_objects')))

    S3_BUCKET = os.getenv('S3_BUCKET', '')
    S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL', '')
    S3_ACCESS_KEY_ID = os.getenv('S3_ACCESS_KEY_ID', '')
    S3_SECRET_ACCESS_KEY = os.getenv('S3_SECRET_ACCESS_KEY', '')
    S3_REGION = os.getenv('S3_REGION', 'us-east-1')


settings = Settings()
# Tạo thư mục local storage nếu chưa có, để tránh lỗi khi API hoặc worker ghi đè
settings.LOCAL_OBJECT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
