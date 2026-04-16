"""
-------
contextvars là module của Python dùng để lưu dữ liệu theo ngữ cảnh thực thi hiện tại. Mỗi request đều có một bản sao ngữ cảnh riêng
- mỗi request / job có một trace_id
- trace_id được truyền xuyên qua API -> queue -> worker -> event -> websocket
- log ở mọi service đều có thể ghép lại theo trace_id
Ví dụ:  
```python
import asyncio
import contextvars

user_var = contextvars.ContextVar("user", default="-")

async def handle_request(name: str, delay: float):
    user_var.set(name)
    await asyncio.sleep(delay)
    print(f"Request của {name} thấy user_var =", user_var.get())

async def main():
    await asyncio.gather(
        handle_request("Alice", 1),
        handle_request("Bob", 0.5),
    )

asyncio.run(main())
```
Kết quả ta nhận được sẽ là:  
```bash
Request của Bob thấy user_var = Bob
Request của Alice thấy user_var = Alice
```
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
    Sau dấu `*` thì bắt buộc các tham số phía sau phải truyền vào bằng `keyword argument`  
    Ví dụ: `set_trace_context(trace_id="abc", user_id="u1")`  
    Chứ không được gọi: `set_trace_context("abc", "t1", "u1", "j1")`
    """
    # Cập nhật các giá trị tương ứng vào ContextVar
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
    tình trạng rò rỉ context cũ sang request mới.
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
