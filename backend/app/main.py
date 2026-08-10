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
