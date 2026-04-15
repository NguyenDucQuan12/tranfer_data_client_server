"""
----------
Sample tracing đơn giản dựa trên correlation id.

Đây chưa phải distributed tracing đầy đủ kiểu OpenTelemetry,
nhưng nó đủ để bạn hiểu nguyên lý:
- mỗi request / job có một trace_id
- trace_id được truyền xuyên qua API -> queue -> worker -> event -> websocket
- log ở mọi service đều có thể ghép lại theo trace_id
"""

from __future__ import annotations

import contextvars
import uuid

_trace_id_var = contextvars.ContextVar('trace_id', default='-')
_tenant_id_var = contextvars.ContextVar('tenant_id', default='-')
_user_id_var = contextvars.ContextVar('user_id', default='-')
_job_id_var = contextvars.ContextVar('job_id', default='-')



def new_trace_id() -> str:
    """
    Sinh trace id mới bằng UUID hex.
    """
    return uuid.uuid4().hex


def set_trace_context(*, trace_id: str | None = None, tenant_id: str | None = None, user_id: str | None = None, job_id: str | None = None) -> None:
    """
    Gán các giá trị context hiện tại.
    Dùng trong:

    API middleware
    WebSocket handler
    worker trước khi xử lý job
    """
    if trace_id is not None:
        _trace_id_var.set(trace_id)
    if tenant_id is not None:
        _tenant_id_var.set(tenant_id)
    if user_id is not None:
        _user_id_var.set(user_id)
    if job_id is not None:
        _job_id_var.set(job_id)


def clear_trace_context() -> None:
    """
    Reset context về -.
    """
    _trace_id_var.set('-')
    _tenant_id_var.set('-')
    _user_id_var.set('-')
    _job_id_var.set('-')


def get_trace_context() -> dict[str, str]:
    """
    Trả dict context hiện tại.
    Dùng bởi logger formatter để log tự động có trace_id, tenant_id, user_id, job_id.
    """
    return {
        'trace_id': _trace_id_var.get(),
        'tenant_id': _tenant_id_var.get(),
        'user_id': _user_id_var.get(),
        'job_id': _job_id_var.get(),
    }
