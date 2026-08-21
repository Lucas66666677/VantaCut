"""Real-JWT regression coverage for the M3 core/workspace route slice.

The routes are loaded directly to keep this security slice independent of the
full application's optional ML imports. Authentication is never overridden:
each request uses a genuine access token decoded by get_current_user against
the database session.
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
from app.models.entities import Clip, MediaAsset, MediaStatus, MediaType, Project, Timeline, User, WorkspacePreference

API = "/api/v1"


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m3_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cloud_drafts = _load("cloud_drafts")
workspace = _load("workspace")
workspace_context = _load("workspace_context")
media_lifecycle = _load("media_lifecycle")
renders = _load("renders")


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in (cloud_drafts, workspace, workspace_context, media_lifecycle, renders):
        app.include_router(module.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    row = User(email=f"m3-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(row); db_session.flush()
    return row


def _owned_graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M3 identity test")
    db_session.add(project); db_session.flush()
    timeline = Timeline(project_id=project.id, name="M3 timeline", settings_json={})
    asset = MediaAsset(project_id=project.id, filename="m3.mp4", storage_key="m3/source.mp4", media_type=MediaType.VIDEO, status=MediaStatus.READY)
    db_session.add_all([timeline, asset]); db_session.flush()
    clip = Clip(timeline_id=timeline.id, source_asset_id=asset.id, source_start=0, source_end=1, order_index=0)
    db_session.add(clip); db_session.flush()
    return project, timeline, asset, clip


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m3_end_user_routes_reject_anonymous_and_invalid_tokens(db_session, headers):
    owner = _user(db_session)
    project, timeline, asset, clip = _owned_graph(db_session, owner)
    client = _client(db_session)
    requests = [
        ("put", f"{API}/timelines/{timeline.id}/cloud-draft", {"timeline": {}}),
        ("get", f"{API}/timelines/{timeline.id}/cloud-draft", None),
        ("post", f"{API}/timelines/{timeline.id}/mobile-preview-handoff", {}),
        ("get", f"{API}/projects/{project.id}/workspace", None),
        ("put", f"{API}/projects/{project.id}/workspace", {"layout": {"modules": {}}}),
        ("get", f"{API}/timelines/{timeline.id}/clips/{clip.id}/workspace-context", None),
        ("post", f"{API}/projects/{project.id}/storage/mark-completed", {}),
        ("get", f"{API}/projects/{project.id}/storage/status", None),
        ("post", f"{API}/projects/{project.id}/storage/hydrate", {}),
        ("get", f"{API}/timelines/render-jobs/{uuid.uuid4()}/download-url", None),
        ("post", f"{API}/timelines/{timeline.id}/render", {}),
        ("post", f"{API}/timelines/{timeline.id}/omnichannel-export", {}),
        ("get", f"{API}/timelines/{timeline.id}/omnichannel-export/{uuid.uuid4()}", None),
    ]
    for method, url, body in requests:
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_cloud_workspace_and_context_use_the_authenticated_owner_only(db_session):
    owner = _user(db_session); attacker = _user(db_session)
    project, timeline, _, clip = _owned_graph(db_session, owner)
    client = _client(db_session)
    body = {"timeline": {"clips": []}, "editor_state": {}}
    assert client.put(f"{API}/timelines/{timeline.id}/cloud-draft", json=body, headers=_auth(attacker)).status_code == 403
    assert client.put(f"{API}/timelines/{timeline.id}/cloud-draft", json={**body, "user_id": str(owner.id)}, headers=_auth(attacker)).status_code == 403
    assert client.put(f"{API}/timelines/{timeline.id}/cloud-draft", json=body, headers=_auth(owner)).status_code == 200
    assert client.get(f"{API}/timelines/{timeline.id}/cloud-draft", headers=_auth(owner)).status_code == 200
    assert client.put(f"{API}/projects/{project.id}/workspace", json={"layout": {"modules": {}}, "user_id": str(owner.id)}, headers=_auth(attacker)).status_code == 403
    assert client.put(f"{API}/projects/{project.id}/workspace", json={"layout": {"modules": {}}}, headers=_auth(owner)).status_code == 200
    assert db_session.query(WorkspacePreference).filter_by(project_id=project.id, user_id=owner.id).one_or_none() is not None
    assert client.get(f"{API}/timelines/{timeline.id}/clips/{clip.id}/workspace-context", headers=_auth(attacker)).status_code == 403
    assert client.get(f"{API}/timelines/{timeline.id}/clips/{clip.id}/workspace-context?user_id={owner.id}", headers=_auth(attacker)).status_code == 403
    assert client.get(f"{API}/timelines/{timeline.id}/clips/{clip.id}/workspace-context", headers=_auth(owner)).status_code == 200
