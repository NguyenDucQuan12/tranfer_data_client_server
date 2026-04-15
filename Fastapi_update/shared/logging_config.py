"""
-----------------
Cấu hình logging thống nhất cho mọi service.

Điểm quan trọng:
- formatter có trace_id / tenant_id / user_id / job_id
- worker và notification service có thể log cùng một trace_id với API
"""

from __future__ import annotations

import logging
from typing import Any

from shared.tracing import get_trace_context


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Đọc nội dung từ shared.tracing.get_trace_context để gắn vào log record
        ctx = get_trace_context()
        record.trace_id = ctx['trace_id']
        record.tenant_id = ctx['tenant_id']
        record.user_id = ctx['user_id']
        record.job_id = ctx['job_id']
        return True



def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt='%(asctime)s | %(levelname)s | trace=%(trace_id)s tenant=%(tenant_id)s user=%(user_id)s job=%(job_id)s | %(name)s | %(message)s'
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)



def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
