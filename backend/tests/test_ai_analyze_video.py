"""Batch 1 security tests for POST /ai/analyze-video (app/api/v1/ai.py).

Mocks ONLY the external, paid AI provider. Identity (get_current_user) and
ownership (_resolve_owned_media_asset) are exercised for real against the
test database — never mocked — per the batch's explicit test policy.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, User


def _load_ai_router():
    """Load app/api/v1/ai.py directly, bypassing `app.api.__init__` and
    `app.api.v1.__init__` — same reasoning as
    tests/conftest.py::_load_auth_router: app/api/__init__.py eagerly imports
    all ~75 v1 routers, several of which need packages this CI test slice
    doesn't install. ai.py's own imports (app.ai.providers.*, app.auth.*,
    app.db.session, app.models.entities) all resolve fine on their own —
    confirmed by reading every app/ai/providers/*.py module: their only
    module-level third-party import is httpx (already installed for this CI
    slice); torch/langchain/open_clip/etc. are all imported lazily inside
    function bodies that these tests never call.
    """
    ai_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "ai.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_ai_router", ai_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ai_module = _load_ai_router()


class _RecordingProvider:
    """Stand-in for the real (paid, external) MultimodalProvider. Records every
    call so tests can assert an unauthorized request never reaches it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def analyze_video(self, video_uri: str, prompt: str, **kwargs):
        self.calls.append((video_uri, prompt))
        return {"summary": "mock analysis", "video_uri": video_uri}


@pytest.fixture()
def provider():
    return _RecordingProvider()


@pytest.fixture()
def app_client(db_session, provider):
    app = FastAPI()
    app.include_router(_ai_module.router, prefix="/api/v1")

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # Override ONLY the external provider boundary. get_current_user is
    # deliberately left un-overridden so every test exercises the real
    # token-decode + DB user lookup.
    app.dependency_overrides[_ai_module.vision_provider_dependency] = lambda: provider
    with TestClient(app) as test_client:
        yield test_client


def _make_user(db_session) -> User:
    user = User(email=f"batch1-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_owned_media_asset(db_session, owner: User) -> MediaAsset:
    project = Project(owner_id=owner.id, name="Batch 1 test project")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(
        project_id=project.id,
        filename="clip.mp4",
        storage_key=f"batch1/{uuid.uuid4().hex}.mp4",
        media_type=MediaType.VIDEO,
        status=MediaStatus.READY,
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def test_anonymous_request_rejected_and_provider_never_called(app_client, provider):
    response = app_client.post(
        "/api/v1/ai/analyze-video", params={"video_uri": "whatever", "prompt": "describe this"}
    )
    assert response.status_code == 401
    assert provider.calls == []


def test_invalid_token_rejected_and_provider_never_called(app_client, provider):
    response = app_client.post(
        "/api/v1/ai/analyze-video",
        params={"video_uri": "whatever", "prompt": "describe this"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert provider.calls == []


def test_non_owner_rejected_and_provider_never_called(app_client, provider, db_session):
    owner = _make_user(db_session)
    asset = _make_owned_media_asset(db_session, owner)
    other_user = _make_user(db_session)
    token = create_access_token(other_user.id)

    response = app_client.post(
        "/api/v1/ai/analyze-video",
        params={"video_uri": asset.storage_key, "prompt": "describe this"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert provider.calls == []


def test_unknown_video_uri_rejected_and_provider_never_called(app_client, provider, db_session):
    user = _make_user(db_session)
    token = create_access_token(user.id)

    response = app_client.post(
        "/api/v1/ai/analyze-video",
        params={"video_uri": "no-such-storage-key", "prompt": "describe this"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert provider.calls == []


def test_owner_request_reaches_provider(app_client, provider, db_session):
    owner = _make_user(db_session)
    asset = _make_owned_media_asset(db_session, owner)
    token = create_access_token(owner.id)

    response = app_client.post(
        "/api/v1/ai/analyze-video",
        params={"video_uri": asset.storage_key, "prompt": "describe this"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert provider.calls == [(asset.storage_key, "describe this")]


def test_disabled_owner_rejected_and_provider_never_called(app_client, provider, db_session):
    owner = _make_user(db_session)
    asset = _make_owned_media_asset(db_session, owner)
    owner.is_active = False
    db_session.flush()
    token = create_access_token(owner.id)

    response = app_client.post(
        "/api/v1/ai/analyze-video",
        params={"video_uri": asset.storage_key, "prompt": "describe this"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert provider.calls == []
