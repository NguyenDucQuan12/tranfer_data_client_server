# FastAPI + Worker + Python Client (JWT auth, per-user stream, retry/DLQ, metrics, notification service, PostgreSQL)

## Kiến trúc mới

```text
Python client / Web dashboard
        |                        |                 \ WebSocket (JWT)
        v                  v
   API service         Notification service
   :8000               :8001
      |                    |
      | create job         | đọc Redis Stream theo user / tenant
      |                    | và đẩy event cho client
      v                    |
      Redis queue <--------+
      Redis retry zset
      Redis dead-letter list
      Redis per-user stream
      Redis per-tenant stream
      |
      v
    Worker
      |
      +--> PostgreSQL (metadata job bền vững)
      +--> Local object storage / S3-compatible storage (file upload, file kết quả)
```

---

### 1) Authentication thật cho WebSocket và API upload
- **Tệp**: `shared/auth.py`
- **Hàm chính**:
  - `authenticate_demo_user()`
  - `create_access_token()`
  - `get_current_identity_from_request()`
  - `get_current_identity_from_websocket()`
- **API cấp token**:
  - `api_service/main.py` -> `issue_token()` tại `POST /v1/auth/token`
- **Cách xử lý**:
  - API upload không còn nhận `user_id` từ form nữa.
  - Client phải login để lấy JWT.
  - API và WebSocket đều giải JWT để lấy `tenant_id`, `user_id`, `role`.
  - Worker và notification service tin vào claim trong token chứ không tin dữ liệu client tự khai báo.

### 2) Phân stream theo user / tenant để giảm lọc event
- **Tệp**: `shared/redis_keys.py`, `shared/events.py`, `notification_service/main.py`
- **Hàm chính**:
  - `user_stream_key()`
  - `tenant_stream_key()`
  - `emit_job_event()`
  - `replay_stream()`
  - `stream_live_events()`
- **Cách xử lý**:
  - Mỗi user có stream riêng: `prod_demo:stream:tenant:<tenant_id>:user:<user_id>`
  - Mỗi tenant có stream dashboard riêng: `prod_demo:stream:tenant:<tenant_id>:dashboard`
  - Notification service đọc **đúng stream cần thiết**, không đọc stream chung rồi lọc từng event như bản cũ.

### 3) Giới hạn dung lượng file upload
- **Tệp**: `shared/storage.py`, `api_service/main.py`
- **Hàm chính**:
  - `LocalObjectStorage.save_upload_file()`
  - `S3ObjectStorage.save_upload_file()`
  - `upload_job()`
- **Cách xử lý**:
  - File được đọc theo chunk.
  - Tổng số byte được cộng dồn.
  - Nếu vượt `MAX_UPLOAD_BYTES`, hệ thống dừng ghi file và trả lỗi `413 Payload Too Large`.

### 4) Retry / dead-letter queue cho worker
- **Tệp**: `worker/worker.py`
- **Hàm chính**:
  - `schedule_retry()`
  - `move_due_retries_back_to_main_queue()`
  - `send_to_dead_letter_queue()`
  - `process_one_job()`
- **Cách xử lý**:
  - Nếu job lỗi và số lần thử chưa vượt ngưỡng, worker không bỏ job ngay.
  - Worker tính `retry_at` theo exponential backoff rồi đưa job vào Redis Sorted Set.
  - Vòng lặp worker định kỳ quét retry zset để đưa các job đến hạn trở lại queue chính.
  - Nếu vượt quá `MAX_RETRY_ATTEMPTS`, job bị đẩy vào DLQ để phân tích thủ công.

### 5) Logging / tracing / metrics
- **Tệp**: `shared/logging_config.py`, `shared/tracing.py`, `shared/metrics.py`, `api_service/main.py`, `worker/worker.py`, `notification_service/main.py`
- **Hàm chính**:
  - `configure_logging()`
  - `set_trace_context()` / `clear_trace_context()`
  - metrics trong `shared/metrics.py`
  - middleware `request_context_middleware()` trong `api_service/main.py`
- **Cách xử lý**:
  - Mỗi request có `trace_id` / `request_id`.
  - Worker giữ `trace_id` của job để log cùng chuỗi xử lý.
  - Metrics Prometheus có sẵn tại `/metrics` ở API service và notification service.
  - Sample này dùng **application-level tracing** bằng correlation id. Nó đủ để bạn học flow; nếu cần distributed tracing đầy đủ, bạn có thể gắn OpenTelemetry ngay tại các hook đã comment.

### 6) Tách riêng notification service nếu hệ thống lớn
- **Tệp**: `notification_service/main.py`
- **Hàm chính**:
  - `websocket_user_events()`
  - `websocket_tenant_dashboard()`
- **Cách xử lý**:
  - API service chỉ nhận upload / đọc trạng thái.
  - Notification service chỉ giữ WebSocket và replay stream.
  - Khi lượng WebSocket lớn, bạn scale notification service riêng mà không ảnh hưởng API upload.

### 7) PostgreSQL hoặc object storage cho dữ liệu bền vững hơn
- **Tệp**: `shared/db.py`, `shared/models.py`, `shared/storage.py`, `api_service/main.py`, `worker/worker.py`
- **Hàm chính**:
  - `init_db()`
  - `create_job_record()`
  - `update_job_record()`
  - `get_job_record()`
  - `get_storage_backend()`
- **Cách xử lý**:
  - PostgreSQL lưu metadata job, trạng thái, số lần retry, lỗi, đường dẫn object key.
  - Object storage lưu file upload và file kết quả. Demo chạy local filesystem để bạn thử ngay.
  - `S3ObjectStorage` đã có skeleton để bạn chuyển sang MinIO / S3-compatible storage.

### 8) Retention policy cho Redis Stream
- **Tệp**: `shared/events.py`, `shared/config.py`
- **Hàm chính**:
  - `emit_job_event()`
- **Cách xử lý**:
  - Khi ghi event, hệ thống dùng `MAXLEN` cấu hình riêng cho user stream và tenant stream.
  - Điều này giữ stream không tăng vô hạn.
  - Retention ở sample là **theo số lượng event**. Nếu bạn muốn retention theo thời gian, bạn có thể mở rộng sang `XTRIM MINID`.

---

## Demo users để login

Các user demo được khai báo trong `shared/auth.py`:

- `alice` / `alicepass` -> tenant `tenant_a`, role `user`
- `admin_a` / `adminpass` -> tenant `tenant_a`, role `admin`
- `bob` / `bobpass` -> tenant `tenant_b`, role `user`

> Đây là user demo để bạn chạy thử JWT flow. Production nên lưu user + password hash trong DB.

---

## Cách chạy

### 1. Chạy hạ tầng

```bash
docker compose up -d
```

### 2. Tạo virtualenv và cài package

```bash
python -m venv .venv
source .venv/bin/activate   # Windows dùng .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Copy file env

```bash
cp .env.example .env
```

### 4. Chạy API service

```bash
uvicorn api_service.main:app --reload --port 8000
```

### 5. Chạy notification service

```bash
uvicorn notification_service.main:app --reload --port 8001
```

### 6. Chạy worker

```bash
python -m worker.worker
```

Worker sẽ mở metrics endpoint riêng ở `http://127.0.0.1:8010/metrics`.

### 7. Lấy token demo

```bash
python -m client.get_token alice alicepass
python -m client.get_token admin_a adminpass
```

### 8. Chạy Python client nghe event user

```bash
python -m client.client --username alice --password alicepass
```

### 9. Chạy dashboard client nghe event tenant

```bash
python -m client.dashboard_client --username admin_a --password adminpass
```

### 10. Upload file

```bash
python -m client.upload_demo --username alice --password alicepass client/sample_input.txt
```

---

## Endpoint chính

### API service (:8000)
- `POST /v1/auth/token` -> lấy JWT
- `POST /v1/jobs/upload` -> upload file
- `GET /v1/jobs/{job_id}` -> xem trạng thái job từ PostgreSQL
- `GET /v1/jobs/{job_id}/result` -> tải file kết quả từ object storage
- `GET /metrics` -> metrics Prometheus

### Notification service (:8001)
- `WS /ws/events` -> stream riêng của user
- `WS /ws/tenant-dashboard` -> stream dashboard của tenant (role admin)
- `GET /metrics` -> metrics Prometheus

---

## Lưu ý quan trọng

1. **Auth trong sample là JWT thật**, nhưng user store vẫn là demo in-memory cho dễ chạy.
2. **Tracing trong sample là correlation tracing**, chưa bật exporter ra Jaeger/Tempo/OTLP.
3. **Storage backend mặc định là local filesystem**. Bạn có thể chuyển sang MinIO/S3 bằng config.
4. **Retention stream** hiện dùng maxlen. Với volume lớn, bạn có thể bổ sung time-based trim.

Worker metrics: `http://127.0.0.1:8010/metrics`
