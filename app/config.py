"""
---------
Tập trung toàn bộ phần cấu hình để:
1. các file khác không phải lặp lại tên key Redis, thư mục lưu trữ,...
2. bạn có thể đổi cấu hình ở một nơi duy nhất
3. dễ hiểu luồng chạy của project

Mọi giá trị đều có default để demo chạy được ngay.
Khi đưa lên production, bạn nên cấu hình qua biến môi trường.
"""

from __future__ import annotations

import os
from pathlib import Path


class Settings:
    # Thư mục gốc của project
    BASE_DIR = Path(__file__).resolve().parent.parent

    # Redis: dùng chung cho FastAPI và worker
    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    # Các key Redis
    JOB_QUEUE_KEY = os.getenv("JOB_QUEUE_KEY", "demo:job_queue")
    EVENT_STREAM_KEY = os.getenv("EVENT_STREAM_KEY", "demo:job_events")

    # Prefix để lưu trạng thái job theo dạng:
    # demo:job:<job_id>
    JOB_KEY_PREFIX = os.getenv("JOB_KEY_PREFIX", "demo:job:")

    # Thư mục lưu file upload và kết quả xử lý
    STORAGE_DIR = BASE_DIR / "storage"
    UPLOAD_DIR = STORAGE_DIR / "uploads"
    RESULT_DIR = STORAGE_DIR / "results"

    # Mỗi lần server đọc stream sẽ block tối đa bao lâu (ms)
    # Hết thời gian mà không có event mới thì server gửi heartbeat cho client.
    STREAM_BLOCK_MS = int(os.getenv("STREAM_BLOCK_MS", "15000"))

    # Một lần replay tối đa bao nhiêu event cũ.
    # Đặt 100 là đủ cho demo; production có thể phân trang / streaming tiếp.
    REPLAY_BATCH_SIZE = int(os.getenv("REPLAY_BATCH_SIZE", "100"))


settings = Settings()

# Tạo sẵn thư mục để tránh lỗi "folder does not exist"
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.RESULT_DIR.mkdir(parents=True, exist_ok=True)
