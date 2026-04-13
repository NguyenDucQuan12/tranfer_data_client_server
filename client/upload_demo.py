"""
upload_demo.py
--------------
Script nhỏ để bạn upload file thử nghiệm từ Python.

Cách dùng:
python client/upload_demo.py path/to/file.txt

Script này chỉ để tạo job demo.
Client nhận event real-time vẫn là file client.py riêng.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

SERVER_HTTP_BASE = "http://127.0.0.1:8000"
USER_ID = "user_demo_01"


def main() -> None:
    if len(sys.argv) < 2:
        print("Cách dùng: python client/upload_demo.py path/to/file")
        raise SystemExit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"Không tìm thấy file: {file_path}")
        raise SystemExit(1)

    with file_path.open("rb") as f:
        response = requests.post(
            f"{SERVER_HTTP_BASE}/v1/jobs/upload",
            data={"user_id": USER_ID},
            files={"file": (file_path.name, f)},
            timeout=60,
        )

    print("Status code:", response.status_code)
    print(response.text)


if __name__ == "__main__":
    main()
