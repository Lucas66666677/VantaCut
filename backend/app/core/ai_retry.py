import random
from typing import Any

from app.core.progress import publish_project_status


RETRYABLE_STATUS_CODES = {429, 500, 503, 504}
MAX_AI_RETRIES = 5
MAX_BACKOFF_SECONDS = 5 * 60


def _status_code_from_exception(exc: Exception) -> int | None:
    for candidate in (exc, getattr(exc, "response", None)):
        value = getattr(candidate, "status_code", None) if candidate is not None else None
        if isinstance(value, int):
            return value
    return None


def is_retryable_ai_error(exc: Exception) -> bool:
    """Recognise vendor HTTP failures and transient network failures without retrying bad requests."""
    status_code = _status_code_from_exception(exc)
    if status_code in RETRYABLE_STATUS_CODES:
        return True
    error_name = exc.__class__.__name__.lower()
    if "ratelimit" in error_name or "timeout" in error_name or "connection" in error_name:
        return True
    return isinstance(exc, (TimeoutError, ConnectionError))


def backoff_seconds(retry_number: int) -> int:
    """Exponential backoff with one-second jitter: 2, 4, 8... seconds up to five minutes."""
    return min(MAX_BACKOFF_SECONDS, 2 ** max(1, retry_number) + random.randint(0, 1))


def retry_ai_task(
    task: Any,
    exc: Exception,
    *,
    project_id: str,
    stage: str,
    message: str,
    job_id: str | None = None,
) -> bool:
    """Publish retry state then raise Celery Retry when the failure is transient."""
    if not is_retryable_ai_error(exc) or task.request.retries >= MAX_AI_RETRIES:
        return False
    retry_number = task.request.retries + 1
    countdown = backoff_seconds(retry_number)
    publish_project_status(
        project_id,
        progress=0,
        stage=f"{stage}_retrying",
        status="processing",
        message=f"{message}；{countdown} 秒後重試（第 {retry_number}/{MAX_AI_RETRIES} 次）",
        job_id=job_id or task.request.id,
    )
    raise task.retry(exc=exc, countdown=countdown, max_retries=MAX_AI_RETRIES)
