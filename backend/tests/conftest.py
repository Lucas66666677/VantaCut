from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db.session import Base, engine, get_db


def _load_auth_router():
    """Load app/api/v1/auth.py directly, bypassing `app.api.__init__` and
    `app.api.v1.__init__`.

    A plain `from app.api.v1.auth import router` first executes
    `app/api/__init__.py`, which eagerly imports all ~75 v1 routers —
    several (e.g. app/api/v1/media.py -> app/services/storage.py) require
    `boto3` and other packages this CI test slice deliberately doesn't
    install (see the `client` fixture below). Loading the auth module by
    file path sidesteps package `__init__.py` execution entirely, so only
    auth.py's own imports (app.auth.*, app.core.*, app.db.session,
    app.models.entities — all already installed) need to resolve. Once
    routes are migrated onto get_current_user (see
    vantacut-auth-route-map.md), this can go back to a normal import
    against the real app.main app.
    """
    auth_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "auth.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_auth_router", auth_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.router


auth_router = _load_auth_router()


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    """CI runs `alembic upgrade head` before pytest, so this is a no-op there.
    It exists only so a developer can run these tests against a throwaway local
    Postgres without running Alembic by hand first."""
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    # Deliberately a minimal app (just the auth router), not the full app.main —
    # app.main pulls in all ~75 v1 routers, several of which import heavy ML
    # dependencies (torch, mediapipe, ...) at module level that this test slice
    # has no need to install. Once routes are migrated onto get_current_user
    # (see vantacut-auth-route-map.md), route-protection tests should exercise
    # the real app.main app instead.
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
