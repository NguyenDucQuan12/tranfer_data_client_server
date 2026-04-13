# FastAPI + Worker + Python Client (Auto Reconnect + Replay Event)

1. **Client upload file** lên FastAPI
2. FastAPI **đẩy job vào Redis queue**
3. **Worker** lấy job ra xử lý
4. Worker ghi **event** vào **Redis Stream**
5. **Python client** kết nối WebSocket tới FastAPI để nhận event real-time
6. Nếu client mất kết nối, khi reconnect nó sẽ gửi `last_event_id` để server **gửi bù event bị lỡ**

---

> Làm sao để chương trình Python client luôn nhận được thông báo mới nhất từ server?

Câu trả lời là:

- **Không polling DB liên tục**
- **Không chỉ dựa vào WebSocket sống mãi**
- **Phải có event store để replay khi reconnect**

Trong demo này:

- **Redis List** dùng làm queue
- **Redis Stream** dùng làm event store
- **WebSocket** dùng để đẩy event real-time cho client
- **Redis Hash** dùng để lưu trạng thái cuối của job

---

## Cấu trúc thư mục

```text
fastapi-worker-python-client-sample/
├─ app/
│  ├─ __init__.py
│  ├─ config.py
│  └─ schemas.py
├─ worker/
│  ├─ __init__.py
│  └─ worker.py
├─ client/
│  ├─ client.py
│  ├─ upload_demo.py
│  └─ sample_input.txt
├─ storage/
│  ├─ uploads/
│  └─ results/
├─ main.py
├─ docker-compose.yml
├─ requirements.txt
└─ README.md
```

---

## Yêu cầu

- Python 3.10+
- Docker
- Redis được khởi chạy trên Docker hoặc cài đặt trực tiếp

---

## Cách chạy nhanh nhất

### 1. Chạy Redis

Nếu có Docker:

```bash
docker compose up -d
```

Kiểm tra Redis đã chạy:

```bash
docker ps
```

---

### 2. Tạo môi trường ảo và cài thư viện

```bash
python -m venv venv_project --prompt="venv camera"
source venv_project/bin/activate
pip install -r requirements.txt
```
---

### 3. Chạy FastAPI server

```bash
# Tùy theo đường dẫn tệp main.py của bạn. Vi dụ nếu main.py nằm ở thư mục gốc:
uvicorn main:app --reload  
# Nếu tệp main.py nằm trong thư mục app, bạn sẽ cần chỉ định module:
uvicorn app.main:app --reload
```
Hoặc có thể dùng:

```bash
fastapi dev main.py
```

Server mặc định chạy ở:

- `http://127.0.0.1:8000`

Xem tài liệu tại:  
- `http://127.0.0.1:8000/docs`

---

### 4. Chạy worker

Mở terminal thứ 2:

```bash
python worker\worker.py
```

Bạn sẽ thấy log kiểu:

```text
[WORKER] Worker started
[WORKER] Redis URL: redis://127.0.0.1:6379/0
[WORKER] Queue key: demo:job_queue
```

---

### 5. Chạy Python client nhận event

Mở terminal thứ 3:

```bash
python client/client.py
```

Lần đầu chạy, client chưa có event nào nên sẽ dùng:

- `last_event_id = 0-0`

Nếu sau này client bị tắt rồi mở lại, nó sẽ đọc file `client/client_state.json`
để biết event cuối cùng đã nhận là gì.

---

### 6. Upload một file để tạo job

Mở terminal thứ 4:

```bash
python client\upload_demo.py client/sample_input.txt
```

Trong đó `client/sample_input.txt` là đường dẫn file bạn muốn upload. Bạn có thể thay bằng file khác nếu muốn.

Bạn sẽ thấy:

- Server nhận file
- Worker lấy job
- Client nhận các event:
  - `job_queued`
  - `job_processing`
  - `job_progress`
  - `job_completed`

---

## Cách kiểm tra tính năng reconnect

1. Chạy đủ 4 terminal như trên
2. Upload một file để xác nhận luồng đang chạy
3. Tắt client bằng `Ctrl + C`
4. Trong lúc client đang tắt, upload thêm 1 hoặc 2 file nữa
5. Mở lại client:

```bash
python client/client.py
```

Client sẽ gửi `last_event_id` cũ lên server, và server sẽ **replay**
các event bị lỡ sau event đó.

Đây chính là phần quan trọng nhất giúp client "luôn nhận được thông báo mới nhất".

---

## Các endpoint chính

### Upload file

```http
POST /v1/jobs/upload
```

Form data:

- `user_id`: string
- `file`: file binary

---

### Xem trạng thái job

```http
GET /v1/jobs/{job_id}
```

---

### Lấy file kết quả

```http
GET /v1/jobs/{job_id}/result
```

Chỉ dùng được khi job đã `success`.

---

### WebSocket nhận event theo user

```text
ws://127.0.0.1:8000/ws/users/<user_id>?last_event_id=<event_id>
```

Ví dụ:

```text
ws://127.0.0.1:8000/ws/users/user_demo_01?last_event_id=1713000000000-0
```

---

## Dữ liệu Redis đang được dùng như thế nào?

### 1. Redis List làm queue

Key:

```text
demo:job_queue
```

FastAPI `LPUSH`, worker `BRPOP`.

---

### 2. Redis Hash lưu trạng thái cuối của job

Key:

```text
demo:job:<job_id>
```

Ví dụ lưu:

- status
- progress
- message
- result_path
- error
- timestamps

---

### 3. Redis Stream lưu toàn bộ event theo thời gian

Key:

```text
demo:job_events
```

Mỗi event có thông tin như:

- type
- job_id
- user_id
- status
- progress
- message
- result_url
- error
- created_at

Client không nhớ "trạng thái cuối", mà nhớ **event_id cuối cùng đã nhận**.

---

## Tại sao không dùng chỉ mỗi WebSocket + Pub/Sub?

Vì khi client bị mất mạng đúng lúc event được publish:

- Pub/Sub thuần có thể làm mất thông báo
- client reconnect nhưng không biết đã lỡ gì

Redis Stream giải quyết chuyện này bằng cách cho phép đọc lại event sau một `last_event_id`.

---

## Tại sao server WebSocket đọc từ Stream thay vì worker push trực tiếp đến client?

Vì worker không nên biết:

- client nào đang online
- socket nào đang mở
- frontend / app nào đang nghe

Worker chỉ cần làm 2 việc:

1. xử lý job
2. ghi event

FastAPI server đóng vai trò **notification gateway**:
- nhận event từ stream
- gửi tới đúng client đang kết nối

Đây là kiến trúc dễ mở rộng hơn.

---

## Những chỗ bạn có thể thay thế để đưa vào dự án thật

### Trong `worker/worker.py` sửa hàm analyze_file hiện tại chỉ có logic demo là đọc file text và đếm số dòng.
Bạn có thể thay logic demo bằng:

- OCR
- AI inference
- PDF parsing
- image processing
- video frame analysis
- gửi dữ liệu sang dịch vụ khác

---

### Trong `client/client.py`
Bạn có thể thay `print(...)` bằng:

- cập nhật UI desktop
- đẩy vào queue nội bộ
- hiển thị popup
- ghi log ra file
- gọi callback xử lý riêng

Nếu client là app desktop như PyQt / Tkinter, hãy để WebSocket listener chạy ở `thread` hoặc `asyncio loop` riêng.

---

## Gợi ý production

Khi lên production, bạn nên cân nhắc thêm:

- authentication thật cho WebSocket và API upload
- phân stream theo user / tenant để giảm lọc event
- giới hạn dung lượng file upload
- retry / dead-letter queue cho worker
- logging / tracing / metrics
- tách riêng notification service nếu hệ thống lớn
- PostgreSQL hoặc object storage cho dữ liệu bền vững hơn
- retention policy cho Redis Stream

---

## Tóm tắt ý tưởng cốt lõi

Để Python client **luôn nhận được thông báo mới nhất**, kiến trúc nên là:

- **Queue** để worker xử lý
- **Event store** để không mất thông báo khi reconnect
- **WebSocket** để nhận realtime
- **last_event_id** để replay phần bị lỡ

Nói ngắn gọn:

> **WebSocket để push ngay**
>  
> **Redis Stream để lấy lại phần bị lỡ**
>  
> **Client lưu last_event_id để reconnect an toàn**

---
