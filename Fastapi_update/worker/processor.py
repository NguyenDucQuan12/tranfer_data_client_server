"""
worker/processor.py
------------------
Nơi chứa logic xử lý file thật.

Sample vẫn giữ xử lý đơn giản để tập trung vào kiến trúc queue/retry/auth/notification.
Bạn có thể thay đoạn này bằng OCR, AI inference, parse PDF, ...
"""

from __future__ import annotations

import hashlib
from pathlib import Path



def analyze_bytes(raw: bytes, file_name: str) -> dict:
    result = {
        'file_name': file_name,
        'file_size_bytes': len(raw),
        'sha256': hashlib.sha256(raw).hexdigest(),
    }

    try:
        text = raw.decode('utf-8')
        result['file_kind'] = 'text'
        result['line_count'] = len(text.splitlines())
        result['word_count'] = len(text.split())
        result['char_count'] = len(text)
        result['preview'] = text[:300]
    except UnicodeDecodeError:
        result['file_kind'] = 'binary'
        result['preview_hex'] = raw[:32].hex(' ')

    return result
