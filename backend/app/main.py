import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from sqlalchemy import text

from app.api import api_router
from app.core.config import settings
from app.db.session import SessionLocal

app = FastAPI(title="AI Video Editor API", version="0.1.0")
allowed_origins = [origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def readiness() -> dict[str, str]:
    """Readiness is intentionally stricter than liveness for load balancers."""
    database = SessionLocal()
    redis_client = None
    try:
        database.execute(text("SELECT 1"))
        from redis import Redis

        redis_client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        redis_client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="dependencies are not ready") from exc
    finally:
        database.close()
        if redis_client is not None:
            redis_client.close()
    return {"status": "ready"}


@app.get("/ready/storage")
def storage_readiness_endpoint() -> dict[str, bool]:
    """Object storage is a hard requirement for uploads but not for /ready.

    /ready is what a load balancer polls, so widening it to cover S3 would turn
    a storage outage into a whole-service outage. But every media path in this
    API -- upload URLs, multipart, previews, exports -- goes through
    app.services.storage, and S3_ENDPOINT_URL/S3_ACCESS_KEY/S3_SECRET_KEY all
    have local MinIO development defaults. A deployment that never set them
    answers /health and /ready with 200 while failing every upload, which is
    exactly the state this endpoint makes visible.

    Booleans only, deliberately: no endpoint URL, bucket name or credential is
    echoed back, so this stays safe to expose without authentication.
    """
    from app.services.storage_readiness import storage_readiness

    return storage_readiness()
