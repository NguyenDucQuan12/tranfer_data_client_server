"""
FastAPI server cho demo "upload file -> queue -> worker xử lý -> client Python nhận event mới nhất".

Điểm quan trọng:
1. API nhận file, tạo job và đẩy job vào Redis queue.
2. Worker lấy job từ queue, xử lý và ghi event vào Redis Stream.
3. Client Python kết nối WebSocket tới FastAPI.
4. Khi client bị mất mạng và reconnect, server replay lại các event bị lỡ
   nhờ đọc lại từ Redis Stream bằng last_event_id.

Tại sao dùng Redis Stream thay vì chỉ Pub/Sub?
- Pub/Sub chỉ tốt cho live update.
- Nếu client bị rớt mạng đúng lúc server publish, client có thể mất thông báo.
- Redis Stream cho phép đọc lại các event đã phát sau một event_id nhất định.

=> Với yêu cầu "luôn nhận được thông báo mới nhất", Redis Stream rất phù hợp.
"""

from __future__ import annotations

import json
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
import redis.asyncio as redis

from app.config import settings
from app.schemas import JobCreateResponse, JobStatusResponse

app = FastAPI(title="FastAPI + Worker + Python Client Demo")

# Redis async client dùng cho FastAPI.
# Worker sẽ dùng Redis sync client trong file worker.py.
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


def utc_now_iso() -> str:
    """Trả về thời gian UTC ở định dạng ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def job_key(job_id: str) -> str:
    """Sinh Redis key lưu trạng thái job."""
    return f"{settings.JOB_KEY_PREFIX}{job_id}"


def normalize_empty(value: str | None) -> str | None:
    """
    Redis hash lưu mọi thứ dưới dạng chuỗi.
    Hàm này đổi chuỗi rỗng thành None để API trả ra dễ đọc hơn.
    """
    if value is None or value == "":
        return None
    return value


async def create_event(
    *,
    event_type: str,
    job_id: str,
    user_id: str,
    status: str,
    message: str,
    progress: int,
    result_url: str = "",
    error: str = "",
    filename: str = "",
) -> str:
    """
    Ghi một event vào Redis Stream.
    Stream ID do Redis sinh ra, ví dụ: "1712999999999-0"

    Event này sẽ là "nguồn chân lý" cho client reconnect:
    - client nhớ event_id cuối cùng đã nhận
    - khi reconnect, client gửi last_event_id
    - server đọc lại các event sau ID đó và gửi bù cho client
    """
    payload = {
        "type": event_type,
        "job_id": job_id,
        "user_id": user_id,
        "status": status,
        "message": message,
        "progress": str(progress),
        "result_url": result_url,
        "error": error,
        "filename": filename,
        "created_at": utc_now_iso(),
    }

    # maxlen giúp stream không phình vô hạn trong demo.
    # approximate=True cho phép Redis trim nhanh hơn.
    event_id = await redis_client.xadd(
        settings.EVENT_STREAM_KEY,
        payload,
        maxlen=10_000,
        approximate=True,
    )
    return event_id


async def save_upload_file(upload_file: UploadFile, destination: Path) -> None:
    """
    Ghi file upload xuống disk theo kiểu đọc từng chunk nhỏ.
    Cách này an toàn hơn việc đọc toàn bộ file vào RAM một lần.
    """
    with destination.open("wb") as target:
        while True:
            chunk = await upload_file.read(1024 * 1024)  # 1 MB / chunk
            if not chunk:
                break
            target.write(chunk)


async def read_job_status(job_id: str) -> dict[str, Any]:
    """
    Đọc trạng thái job từ Redis hash.
    Nếu không có job thì raise 404.
    """
    data = await redis_client.hgetall(job_key(job_id))
    if not data:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy job_id={job_id}")

    return {
        "job_id": data["job_id"],
        "user_id": data["user_id"],
        "filename": data["filename"],
        "status": data["status"],
        "progress": int(data.get("progress", "0")),
        "message": data.get("message", ""),
        "upload_path": data["upload_path"],
        "result_path": normalize_empty(data.get("result_path")),
        "error": normalize_empty(data.get("error")),
        "uploaded_at": data["uploaded_at"],
        "updated_at": data["updated_at"],
        "finished_at": normalize_empty(data.get("finished_at")),
    }


async def replay_missed_events(
    websocket: WebSocket,
    *,
    user_id: str,
    last_event_id: str,
) -> str:
    """
    Replay các event cũ mà client đã bỏ lỡ.

    Quy tắc:
    - client lưu event_id cuối cùng đã xử lý, ví dụ: "1713000000000-0"
    - khi reconnect, client gửi last_event_id đó lên
    - server dùng XRANGE để đọc các event sau mốc đó

    Hàm trả về stream id cuối cùng đã đọc, để bước live tiếp tục từ đúng vị trí.
    """
    cursor = last_event_id

    while True:
        # Dấu "(" nghĩa là lấy event_id lớn hơn cursor, không lấy bằng.
        entries = await redis_client.xrange(
            settings.EVENT_STREAM_KEY,
            min=f"({cursor}",
            max="+",
            count=settings.REPLAY_BATCH_SIZE,
        )

        if not entries:
            break

        for stream_id, fields in entries:
            cursor = stream_id

            # Stream chung cho toàn hệ thống, nên ta lọc theo user_id ở đây.
            # Production scale lớn hơn có thể đổi sang per-user stream.
            if fields.get("user_id") != user_id:
                continue

            event = {
                "event_id": stream_id,
                "type": fields.get("type", ""),
                "job_id": fields.get("job_id", ""),
                "user_id": fields.get("user_id", ""),
                "status": fields.get("status", ""),
                "message": fields.get("message", ""),
                "progress": int(fields.get("progress", "0")),
                "result_url": fields.get("result_url", ""),
                "error": fields.get("error", ""),
                "filename": fields.get("filename", ""),
                "created_at": fields.get("created_at", ""),
            }

            await websocket.send_text(json.dumps(event, ensure_ascii=False))

    return cursor


@app.get("/")
async def root() -> dict[str, str]:
    """Endpoint nhỏ để kiểm tra server đang chạy."""
    return {"message": "Server is running"}


@app.post("/v1/jobs/upload", response_model=JobCreateResponse)
async def upload_job(
    user_id: str = Form(..., description="Định danh client sẽ nhận thông báo"),
    file: UploadFile = File(...),
) -> JobCreateResponse:
    """
    Nhận file từ client, tạo job mới và enqueue vào Redis list.

    Vì đây là demo, user_id do client truyền lên.
    Thực tế production nên lấy user_id từ:
    - JWT token
    - session
    - API key
    - hoặc hệ thống auth riêng
    """
    job_id = uuid.uuid4().hex
    safe_filename = Path(file.filename or "unnamed.bin").name
    saved_path = settings.UPLOAD_DIR / f"{job_id}_{safe_filename}"

    await save_upload_file(file, saved_path)
    await file.close()

    now = utc_now_iso()
    initial_status = {
        "job_id": job_id,
        "user_id": user_id,
        "filename": safe_filename,
        "status": "queued",
        "progress": "0",
        "message": "Job đã được đưa vào hàng đợi",
        "upload_path": str(saved_path),
        "result_path": "",
        "error": "",
        "uploaded_at": now,
        "updated_at": now,
        "finished_at": "",
    }

    # Lưu trạng thái khởi tạo để API /status có thể đọc được ngay.
    await redis_client.hset(job_key(job_id), mapping=initial_status)

    # Đưa payload vào Redis queue cho worker xử lý.
    queue_payload = {
        "job_id": job_id,
        "user_id": user_id,
        "filename": safe_filename,
        "upload_path": str(saved_path),
    }
    await redis_client.lpush(settings.JOB_QUEUE_KEY, json.dumps(queue_payload, ensure_ascii=False))

    # Ghi event "queued" vào stream để client đang online cũng biết ngay.
    await create_event(
        event_type="job_queued",
        job_id=job_id,
        user_id=user_id,
        status="queued",
        message="Server đã nhận file và đưa job vào queue",
        progress=0,
        filename=safe_filename,
    )

    return JobCreateResponse(
        job_id=job_id,
        user_id=user_id,
        status="queued",
        message="Upload thành công, job đã vào queue",
        websocket_url=f"ws://127.0.0.1:8000/ws/users/{user_id}",
        status_url=f"/v1/jobs/{job_id}",
        result_url=f"/v1/jobs/{job_id}/result",
    )


@app.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Trả trạng thái hiện tại của một job."""
    return JobStatusResponse(**await read_job_status(job_id))


@app.get("/v1/jobs/{job_id}/result", response_model= None)
async def get_job_result(job_id: str) -> JSONResponse | FileResponse:
    """
    Trả kết quả của job.

    Demo này lưu kết quả dưới dạng file JSON.
    Nếu job chưa xong, trả lỗi 409 để client biết chưa lấy được kết quả.
    """
    data = await read_job_status(job_id)
    result_path = data["result_path"]

    if data["status"] != "success":
        raise HTTPException(status_code=409, detail="Job chưa hoàn thành nên chưa có result")

    if not result_path:
        raise HTTPException(status_code=404, detail="Không tìm thấy đường dẫn kết quả")

    result_file = Path(result_path)
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="File kết quả không còn tồn tại")

    media_type = mimetypes.guess_type(result_file.name)[0] or "application/json"
    return FileResponse(result_file, media_type=media_type, filename=result_file.name)


@app.websocket("/ws/users/{user_id}")
async def websocket_user(
    websocket: WebSocket,
    user_id: str,
    last_event_id: str = Query("0-0"),
) -> None:
    """
    Kênh real-time cho Python client.

    Cách dùng:
    - client kết nối tới /ws/users/<user_id>?last_event_id=<event_id_cuoi>
    - server replay các event bị lỡ sau last_event_id
    - sau đó server tiếp tục block đọc Redis Stream để đẩy event mới

    Vì sao endpoint này có thể đảm bảo "mới nhất" tốt hơn polling?
    - client không cần gọi DB liên tục
    - khi reconnect vẫn đọc lại được phần bị bỏ lỡ
    """
    await websocket.accept()

    # Gửi bản tin chào để client biết kết nối đã thành công.
    await websocket.send_text(
        json.dumps(
            {
                "type": "connected",
                "user_id": user_id,
                "message": "WebSocket đã kết nối thành công",
                "last_event_id": last_event_id,
                "server_time": utc_now_iso(),
            },
            ensure_ascii=False,
        )
    )

    current_event_id = last_event_id

    try:
        # Bước 1: replay các event bị lỡ trước khi vào live mode.
        current_event_id = await replay_missed_events(
            websocket,
            user_id=user_id,
            last_event_id=current_event_id,
        )

        # Bước 2: chờ event mới bằng XREAD BLOCK.
        # Ta tiếp tục đọc từ current_event_id hiện tại.
        while True:
            stream_response = await redis_client.xread(
                {settings.EVENT_STREAM_KEY: current_event_id},
                block=settings.STREAM_BLOCK_MS,
                count=10,
            )

            # Không có event mới trong khoảng block time.
            # Gửi heartbeat để:
            # 1. client biết kết nối còn sống
            # 2. server dễ phát hiện client đã ngắt
            if not stream_response:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "heartbeat",
                            "server_time": utc_now_iso(),
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            for _, entries in stream_response:
                for stream_id, fields in entries:
                    # Luôn phải cập nhật current_event_id ngay cả khi event thuộc user khác.
                    # Nếu không, server sẽ đọc lặp lại cùng event đó mãi mãi.
                    current_event_id = stream_id

                    if fields.get("user_id") != user_id:
                        continue

                    event = {
                        "event_id": stream_id,
                        "type": fields.get("type", ""),
                        "job_id": fields.get("job_id", ""),
                        "user_id": fields.get("user_id", ""),
                        "status": fields.get("status", ""),
                        "message": fields.get("message", ""),
                        "progress": int(fields.get("progress", "0")),
                        "result_url": fields.get("result_url", ""),
                        "error": fields.get("error", ""),
                        "filename": fields.get("filename", ""),
                        "created_at": fields.get("created_at", ""),
                    }

                    await websocket.send_text(json.dumps(event, ensure_ascii=False))

    except WebSocketDisconnect:
        # Client đóng kết nối một cách bình thường.
        return
    except Exception as exc:
        # Nếu có lỗi bất ngờ, cố gửi thông báo để client log được nguyên nhân.
        # Nếu websocket đã hỏng thì send_text có thể lỗi tiếp, nên ta bọc try/except.
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "server_error",
                        "message": f"Lỗi phía server websocket: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
        except Exception:
            pass


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Đóng Redis connection gọn gàng khi server tắt."""
    await redis_client.close()
