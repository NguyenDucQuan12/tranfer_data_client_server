"""
----------
Prometheus metrics dùng chung.
File này khai báo metrics Prometheus.

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

UPLOAD_REQUESTS_TOTAL = Counter('upload_requests_total', 'Total upload requests', ['tenant_id'])
UPLOAD_BYTES_TOTAL = Counter('upload_bytes_total', 'Total uploaded bytes', ['tenant_id'])
UPLOAD_REJECTED_TOTAL = Counter('upload_rejected_total', 'Rejected uploads', ['reason'])
JOBS_CREATED_TOTAL = Counter('jobs_created_total', 'Jobs created', ['tenant_id'])
JOBS_COMPLETED_TOTAL = Counter('jobs_completed_total', 'Jobs completed', ['tenant_id'])
JOBS_FAILED_TOTAL = Counter('jobs_failed_total', 'Jobs failed', ['tenant_id'])
JOB_RETRIES_TOTAL = Counter('job_retries_total', 'Retry scheduled', ['tenant_id'])
JOB_DLQ_TOTAL = Counter('job_dlq_total', 'Jobs sent to dead letter queue', ['tenant_id'])
JOB_PROCESSING_SECONDS = Histogram('job_processing_seconds', 'Processing time per job', buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60))
WEBSOCKET_CONNECTIONS = Gauge('websocket_connections', 'Current websocket connections', ['channel'])
WEBSOCKET_EVENTS_SENT_TOTAL = Counter('websocket_events_sent_total', 'Events sent via websocket', ['channel'])


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
