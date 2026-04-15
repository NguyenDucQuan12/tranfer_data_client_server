"""
-----
Kết nối PostgreSQL bằng SQLAlchemy async.

PostgreSQL được dùng cho dữ liệu bền vững hơn Redis:
- metadata job
- trạng thái cuối
- số lần retry
- lỗi
- đường dẫn object key
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.config import settings
from shared.models import Base, JobRecord

# Tạo engine và kết nối tới PostgreSQL
engine = create_async_engine(settings.DATABASE_URL, future=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)



def utc_now() -> datetime:
    """
    Lấy kiểu thời gian hiện tại
    """
    return datetime.now(timezone.utc)


async def init_db() -> None:
    """
    Khởi tạo bảng DB
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_job_record(*, job_id: str, tenant_id: str, user_id: str, filename: str, upload_object_key: str, trace_id: str) -> JobRecord:
    """
    Tạo 1 bản ghi mới khi người dùng upload thành công  
    """
    async with SessionLocal() as session:
        now = utc_now()
        record = JobRecord(
            job_id=job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            filename=filename,
            status='queued',
            progress=0,
            message='Job đã được đưa vào hàng đợi',
            attempts=0,
            upload_object_key=upload_object_key,
            result_object_key=None,
            error=None,
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
            finished_at=None,
        )
        session.add(record)
        await session.commit()
        return record


async def get_job_record(job_id: str) -> JobRecord | None:
    """
    Lấy job theo job id
    """
    async with SessionLocal() as session:
        result = await session.execute(select(JobRecord).where(JobRecord.job_id == job_id))
        return result.scalar_one_or_none()


async def update_job_record(job_id: str, **fields: Any) -> None:
    """
    Cập nhật thông tin các trường khi worker làm việc
    """
    fields['updated_at'] = utc_now()
    async with SessionLocal() as session:
        await session.execute(update(JobRecord).where(JobRecord.job_id == job_id).values(**fields))
        await session.commit()
