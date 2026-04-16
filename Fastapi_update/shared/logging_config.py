"""
-----------------
Cấu hình logging thống nhất cho mọi service.

Điểm quan trọng:
- formatter có trace_id / tenant_id / user_id / job_id
- worker và notification service có thể log cùng một trace_id với API
"""

from __future__ import annotations

import logging
from shared.tracing import get_trace_context


class ContextFilter(logging.Filter):
    """
    Sửa đổi nội dung log trước khi đồng ý cho log đi tiếp để đưa log vào lớp Formatter xử lý
    """
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Mỗi lần gọi logger như: `logger.info("Hello")` thì sẽ tạo ra 1 đối tượng logging  
        Các field có sẵn: record.msg, record.levelname, record.name, record.created, ...
        """
        # Đọc nội dung từ shared.tracing.get_trace_context để lấy context hiện tại.
        ctx = get_trace_context()
        # Thêm 4 field mới vào logging
        record.trace_id = ctx['trace_id']
        record.tenant_id = ctx['tenant_id']
        record.user_id = ctx['user_id']
        record.job_id = ctx['job_id']
        # Cho phép logger được đi tiếp, nếu False thì log bị chặn và không được xử lý hoặc in ra
        return True



def configure_logging(level: int = logging.INFO) -> None:
    """
    Cấu hình toàn bộ logging
    """
    handler = logging.StreamHandler()     # handler ghi log ra stream, thường là console
    handler.addFilter(ContextFilter())    # Gắn Filter vào handler
    # Tạo format cuối cùng cho log trước khi in ra, các field trace_id, ... chỉ được sử dụng khi đã gắn handler, nếu ko sẽ lỗi
    handler.setFormatter(
        logging.Formatter(
            fmt='%(asctime)s | %(levelname)s | trace=%(trace_id)s tenant=%(tenant_id)s user=%(user_id)s job=%(job_id)s | %(name)s | %(message)s'
        )
    )

    root = logging.getLogger()            # Lấy logger gốc của toàn bộ ứng dụng
    root.handlers.clear()                 # Xóa các handlers cũ nếu đã có để tránh bị trùng lặp
    root.addHandler(handler)              # Gắn handler vừa cấu hình vào logger
    root.setLevel(level)                  # Thiết lập mức logger tối thiểu: INFO, WARNING, ERROR, CRITICAL (DEBUG sẽ ko có vì mức này nằm dưới INFO)



def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
