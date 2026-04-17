"""
----------
Prometheus metrics dùng chung.
File này khai báo metrics Prometheus.
trong lúc app chạy, code sẽ tăng/giảm/ghi nhận các metric
Prometheus sẽ gọi endpoint /metrics
endpoint đó trả ra toàn bộ metric hiện tại dưới dạng text
Prometheus đọc text đó và lưu lại để vẽ biểu đồ, cảnh báo, thống kê
Sample này thêm metrics cho:
- số request upload
- dung lượng upload
- số event websocket phát ra
- số retry / số job vào DLQ
- latency xử lý job

--> bạn có thể nhìn được sức khỏe hệ thống mà không query DB.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

"""
Counter là metric chỉ tăng, không giảm  
Gauge là metric có thể tăng, giảm, set một giá trị bất kỳ  
Histogram dùng để đo phân bố của một đại lượng  

"""
# Tạo các bộ đếm
UPLOAD_REQUESTS_TOTAL = Counter('upload_requests_total', 'Total upload requests', ['tenant_id'])
UPLOAD_BYTES_TOTAL = Counter('upload_bytes_total', 'Total uploaded bytes', ['tenant_id'])                                            # Tổng dung lượng đã upload
UPLOAD_REJECTED_TOTAL = Counter('upload_rejected_total', 'Rejected uploads', ['reason'])                                             # Tổng dung lượng upload đã bị từ chối
JOBS_CREATED_TOTAL = Counter('jobs_created_total', 'Jobs created', ['tenant_id'])                                                    # Tổng số job đã tạo
JOBS_COMPLETED_TOTAL = Counter('jobs_completed_total', 'Jobs completed', ['tenant_id'])                                              # Số job đã hoàn thành xử lý
JOBS_FAILED_TOTAL = Counter('jobs_failed_total', 'Jobs failed', ['tenant_id'])                                                       # Số job thất bại
JOB_RETRIES_TOTAL = Counter('job_retries_total', 'Retry scheduled', ['tenant_id'])                                                   # Số job thử lại
JOB_DLQ_TOTAL = Counter('job_dlq_total', 'Jobs sent to dead letter queue', ['tenant_id'])                                            # Số job bị đẩy DLQ (Dead Letter Queue) tức là hàng đợi chứa các job không xử lý nổi sau nhiều lần retry.
JOB_PROCESSING_SECONDS = Histogram('job_processing_seconds', 'Processing time per job', buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60))     # có bao nhiêu job ≤ 0.1 giây, bao nhiêu job ≤ 0.5 giây, bao nhiêu job ≤ 1 giây, ...
WEBSOCKET_CONNECTIONS = Gauge('websocket_connections', 'Current websocket connections', ['channel'])                                 # Số kết nối đang mở
WEBSOCKET_EVENTS_SENT_TOTAL = Counter('websocket_events_sent_total', 'Events sent via websocket', ['channel'])                       # Đếm tổng số event đã gửi qua websocket

# Cách dùng:
# inc(): tăng giá trị, dec(): giảm giát trị, observe() dùng cho histogram
# UPLOAD_REQUESTS_TOTAL.labels(identity.tenant_id).inc() là tăng lên 1 giá trị
# UPLOAD_BYTES_TOTAL.labels("tenant_a").inc(2048) là tăng 2048 
# JOB_PROCESSING_SECONDS.observe(2.3) là vừa ghi nhận một lần xử lý mất 2.3s



def metrics_response() -> Response:
    """
    Hàm xuất metric  
    generate_latest() đọc toàn bộ registry metric hiện tại trong process.  
    Sau đó nó biến các metric đó thành text format chuẩn Prometheus.  
    Cuối cùng Response(...) đóng gói text đó thành HTTP response và gắn đúng content-type
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
