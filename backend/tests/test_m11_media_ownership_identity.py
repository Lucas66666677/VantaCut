"""Real-JWT ownership coverage for the formerly unauthenticated media lifecycle."""
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

API = "/api/v1"


def _load():
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "media.py"
    spec = importlib.util.spec_from_file_location("_vantacut_m11_media", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


media = _load()


class _Task:
    id = "m11-task"


class _Embedding:
    def __init__(self, calls): self.calls = calls
    def embed_text(self, value): self.calls.append("embedding_provider"); return [0.0] * 512


def _client(db_session) -> TestClient:
    app = FastAPI(); app.include_router(media.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m11-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user); db_session.flush()
    return user


def _asset(db_session, project, name, *, status=MediaStatus.UPLOADING, media_type=MediaType.VIDEO, metadata=None):
    asset = MediaAsset(project_id=project.id, filename=name, storage_key=f"m11/{uuid.uuid4()}/{name}", media_type=media_type, mime_type="video/mp4", size_bytes=10, status=status, metadata_json=metadata or {})
    db_session.add(asset); db_session.flush(); return asset


def _graph(db_session, owner):
    project = Project(owner_id=owner.id, name="M11 media ownership")
    db_session.add(project); db_session.flush()
    derived = _asset(db_session, project, "derived.mp4", status=MediaStatus.READY, metadata={"matting_jobs": [{"job_id": "job", "status": "completed", "alpha_webm_key": "m11/alpha.webm"}]})
    part = _asset(db_session, project, "part.mp4")
    complete = _asset(db_session, project, "complete.mp4")
    confirm = _asset(db_session, project, "confirm.png", media_type=MediaType.IMAGE)
    return project, derived, part, complete, confirm


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _requests(graph):
    project, derived, part, complete, confirm = graph
    upload = {"project_id": str(project.id), "filename": "new.mp4", "size_bytes": 10, "content_type": "video/mp4", "media_type": "video"}
    return [
        ("get", f"{API}/media/{derived.id}/derived-previews/job?user_id={project.owner_id}", None),
        ("post", f"{API}/media/search", {"project_id": str(project.id), "query": "test scene"}),
        ("post", f"{API}/media/semantic-grid", {"project_id": str(project.id)}),
        ("post", f"{API}/media/upload-url", upload),
        ("post", f"{API}/media/multipart-upload/initiate", {**upload, "filename": "multipart.mp4"}),
        ("post", f"{API}/media/multipart-upload/part-url", {"asset_id": str(part.id), "upload_id": "upload", "part_number": 1}),
        ("post", f"{API}/media/multipart-upload/complete", {"asset_id": str(complete.id), "upload_id": "upload", "parts": [{"part_number": 1, "etag": "etag"}]}),
        ("post", f"{API}/media/confirm-upload", {"asset_id": str(confirm.id)}),
    ]


def _patch_boundaries(monkeypatch, calls):
    monkeypatch.setattr(media, "get_embedding_provider", lambda: _Embedding(calls))
    monkeypatch.setattr(media, "create_upload_url", lambda *args, **kwargs: calls.append("create_upload_url") or "https://storage/upload")
    monkeypatch.setattr(media, "create_multipart_upload", lambda *args, **kwargs: calls.append("create_multipart_upload") or "upload")
    monkeypatch.setattr(media, "create_multipart_part_url", lambda *args, **kwargs: calls.append("create_multipart_part_url") or "https://storage/part")
    monkeypatch.setattr(media, "complete_multipart_upload", lambda *args, **kwargs: calls.append("complete_multipart_upload"))
    monkeypatch.setattr(media, "object_exists", lambda *args, **kwargs: calls.append("object_exists") or True)
    monkeypatch.setattr(media, "create_download_url", lambda *args, **kwargs: calls.append("create_download_url") or "https://storage/download")
    monkeypatch.setattr(media.process_new_media, "delay", lambda *args, **kwargs: calls.append("process_new_media") or _Task())
    # These tests exercise ownership, not the storage-configured gate: assume a
    # configured deployment so the upload routes reach their identity checks.
    # The gate's own fail-closed behaviour is covered separately below.
    monkeypatch.setattr(media, "storage_is_configured", lambda: True)


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m11_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    calls = []; _patch_boundaries(monkeypatch, calls); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(graph):
        kwargs = {"headers": headers}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)
    assert calls == []


def test_m11_wrong_user_cannot_access_media_or_trigger_side_effects(db_session, monkeypatch):
    calls = []; _patch_boundaries(monkeypatch, calls); owner, attacker = _user(db_session), _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    asset_count = db_session.query(MediaAsset).count()
    for method, url, body in _requests(graph):
        kwargs = {"headers": _auth(attacker)}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 403, (method, url, response.text)
    assert calls == [] and db_session.query(MediaAsset).count() == asset_count


def test_m11_rightful_owner_can_use_every_media_lifecycle_endpoint(db_session, monkeypatch):
    calls = []; _patch_boundaries(monkeypatch, calls); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _requests(graph):
        kwargs = {"headers": _auth(owner)}
        if body is not None: kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in {200, 201}, (method, url, response.status_code, response.text)


def test_m11_upload_urls_are_refused_when_storage_is_unconfigured(db_session, monkeypatch):
    """A deployment that never configured object storage must not hand out an
    upload URL that points at a MinIO nobody started.

    The three URL-issuing routes fail closed with 503 before minting a presigned
    URL and before writing an UPLOADING MediaAsset row, so an authenticated
    owner sees an honest error rather than a browser upload that fails opaquely.
    The identity checks still run first: this is the owner's own project.
    """
    calls = []; _patch_boundaries(monkeypatch, calls)
    monkeypatch.setattr(media, "storage_is_configured", lambda: False)
    owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    project, _derived, part, _complete, _confirm = graph
    upload = {"project_id": str(project.id), "filename": "new.mp4", "size_bytes": 10, "content_type": "video/mp4", "media_type": "video"}
    asset_count = db_session.query(MediaAsset).count()

    url_issuing = [
        ("post", f"{API}/media/upload-url", upload),
        ("post", f"{API}/media/multipart-upload/initiate", {**upload, "filename": "multipart.mp4"}),
        ("post", f"{API}/media/multipart-upload/part-url", {"asset_id": str(part.id), "upload_id": "upload", "part_number": 1}),
    ]
    for method, url, body in url_issuing:
        response = getattr(client, method)(url, headers=_auth(owner), json=body)
        assert response.status_code == 503, (url, response.status_code, response.text)

    # No presigned URL was minted and no UPLOADING row was written.
    assert not ({"create_upload_url", "create_multipart_upload", "create_multipart_part_url"} & set(calls))
    assert db_session.query(MediaAsset).count() == asset_count
