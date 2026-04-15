"""
Lớp object storage abstraction.

Mục đích:
- API và worker không phụ thuộc trực tiếp vào local filesystem
- hôm nay bạn chạy local, ngày mai có thể chuyển sang MinIO / S3
- giới hạn dung lượng upload được đặt ngay tại storage layer để tránh copy code
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
import shutil

from fastapi import HTTPException, UploadFile

from shared.config import settings


@dataclass
class StoredObject:
    """
    object mô tả một file đã lưu
    """
    object_key: str
    size_bytes: int
    absolute_path: str | None = None


class LocalObjectStorage:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    async def save_upload_file(self, *, upload_file: UploadFile, object_key: str, max_bytes: int) -> StoredObject:
        """"
        Xác định path đích.
        Tạo thư mục cha.
        Đọc file theo chunk 1MB.
        Cộng dồn written.
        Nếu vượt max_bytes:
        đóng file
        xóa file dở dang
        ném HTTPException 413
        Nếu không vượt thì ghi chunk.
        Trả StoredObject.
        """
        destination = self.root_dir / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        with destination.open('wb') as target:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    target.close()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f'File vượt giới hạn {max_bytes} bytes')
                target.write(chunk)

        return StoredObject(object_key=object_key, size_bytes=written, absolute_path=str(destination))

    def save_bytes(self, *, data: bytes, object_key: str) -> StoredObject:
        """
        Ghi byte trực tiếp ra file
        """
        destination = self.root_dir / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return StoredObject(object_key=object_key, size_bytes=len(data), absolute_path=str(destination))

    def read_bytes(self, object_key: str) -> bytes:
        """
        Đọc byte từ tệp tin local
        """
        path = self.root_dir / object_key
        return path.read_bytes()

    def resolve_local_path(self, object_key: str) -> Path:
        """
        Chuyển obkect key thành path local
        """
        return self.root_dir / object_key


class S3ObjectStorage:
    """
    Skeleton S3-compatible backend.

    Sample này để sẵn cấu trúc để bạn thấy nơi cần thay đổi nếu chuyển sang MinIO / S3.
    Nó không được dùng mặc định, nhưng code đã đủ rõ để bạn mở rộng.
    """

    def __init__(self) -> None:
        import boto3

        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY or None,
            region_name=settings.S3_REGION,
        )

    async def save_upload_file(self, *, upload_file: UploadFile, object_key: str, max_bytes: int) -> StoredObject:
        """
        Tạm lưu file bằng LocalObjectStorage vào thư mục _tmp_s3_uploads.
        Upload file đó lên S3.
        Xóa file tạm.
        Trả StoredObject.
        """
        temp_backend = LocalObjectStorage(settings.LOCAL_OBJECT_STORAGE_DIR / '_tmp_s3_uploads')
        temp = await temp_backend.save_upload_file(upload_file=upload_file, object_key=object_key, max_bytes=max_bytes)
        path = temp_backend.resolve_local_path(object_key)
        with path.open('rb') as fh:
            self.client.upload_fileobj(fh, self.bucket, object_key)
        path.unlink(missing_ok=True)
        return StoredObject(object_key=object_key, size_bytes=temp.size_bytes, absolute_path=None)

    def save_bytes(self, *, data: bytes, object_key: str) -> StoredObject:
        self.client.put_object(Bucket=self.bucket, Key=object_key, Body=data)
        return StoredObject(object_key=object_key, size_bytes=len(data), absolute_path=None)

    def read_bytes(self, object_key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        return response['Body'].read()

    def resolve_local_path(self, object_key: str) -> Path:
        raise RuntimeError('S3 backend không có local path')



def get_storage_backend():
    if settings.OBJECT_STORAGE_BACKEND.lower() == 's3':
        return S3ObjectStorage()
    return LocalObjectStorage(settings.LOCAL_OBJECT_STORAGE_DIR)
