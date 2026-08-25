#!/bin/bash
set -e
alembic upgrade head

# Render's free tier only grants one service (no separate Background Worker), so the
# Celery worker runs alongside the API in this same container. If it dies, the container
# exits (set -e + wait) and Render restarts the whole service, which brings the worker back too.
celery -A app.worker.celery_app worker --loglevel=info --concurrency=2 --queues=celery,render &
WORKER_PID=$!

gunicorn app.main:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120 --graceful-timeout 30 &
API_PID=$!

wait -n "$WORKER_PID" "$API_PID"
