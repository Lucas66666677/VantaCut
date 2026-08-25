#!/bin/sh
set -e
alembic upgrade head

# Render's free tier only grants one service (no separate Background Worker), so the
# Celery worker runs alongside the API in this same container. Render's Docker Command
# override runs this under plain `sh` (not necessarily bash), so this avoids bashisms
# like `wait -n`: it polls both PIDs and exits as soon as either one dies, which causes
# the whole container to exit and Render to restart both together.
gunicorn app.main:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120 --graceful-timeout 30 &
API_PID=$!

# Start the worker only after the API has actually bound its port. Both processes cold-
# import the same very large ML stack (torch/mediapipe/onnxruntime/langchain/...); on the
# free tier's 0.1 CPU, importing it twice at once starved gunicorn long enough that Render's
# port-scan timed out before it ever bound 8000. Staggering the start avoids that contention.
i=0
while ! curl -sf -o /dev/null http://127.0.0.1:8000/health; do
    i=$((i + 1))
    if [ "$i" -ge 150 ]; then
        break
    fi
    sleep 2
done

# `python -m celery` (not the bare `celery` console-script) guarantees /app is on
# sys.path -- the bare script doesn't reliably add the current directory.
python -m celery -A app.worker.celery_app worker --loglevel=info --concurrency=1 --queues=celery,render &
WORKER_PID=$!

while kill -0 "$WORKER_PID" 2>/dev/null && kill -0 "$API_PID" 2>/dev/null; do
    sleep 5
done

kill "$WORKER_PID" "$API_PID" 2>/dev/null
wait
exit 1
