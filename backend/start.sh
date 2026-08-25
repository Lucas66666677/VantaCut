#!/bin/sh
set -e
alembic upgrade head

# Render's free tier only grants one service (no separate Background Worker), so the
# Celery worker runs alongside the API in this same container. Render's Docker Command
# override runs this under plain `sh` (not necessarily bash), so this avoids bashisms
# like `wait -n`: it polls both PIDs and exits as soon as either one dies, which causes
# the whole container to exit and Render to restart both together.
# `python -m celery` (not the bare `celery` console-script) guarantees /app is on
# sys.path -- the bare script doesn't reliably add the current directory.
python -m celery -A app.worker.celery_app worker --loglevel=info --concurrency=2 --queues=celery,render &
WORKER_PID=$!

gunicorn app.main:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120 --graceful-timeout 30 &
API_PID=$!

while kill -0 "$WORKER_PID" 2>/dev/null && kill -0 "$API_PID" 2>/dev/null; do
    sleep 5
done

kill "$WORKER_PID" "$API_PID" 2>/dev/null
wait
exit 1
