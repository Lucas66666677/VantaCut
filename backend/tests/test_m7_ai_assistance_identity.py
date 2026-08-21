"""Real-JWT ownership coverage for the M7 AI-assistance route family."""
from __future__ import annotations

import copy
import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import (
    AIAnalysis, AgentEditRun, AgentEditStatus, AnalysisType, AvatarProfile, AvatarRenderJob,
    MediaAsset, MediaStatus, MediaType, Project, Timeline, User,
)

API = "/api/v1"
NAMES = ("academic", "agent", "avatar", "behavioral_coach", "inpainting", "screen_focus")


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_vantacut_m7_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


academic, agent, avatar, behavioral_coach, inpainting, screen_focus = map(_load, NAMES)
MODULES = (academic, agent, avatar, behavioral_coach, inpainting, screen_focus)


class _QueuedTask:
    id = "m7-task"


class _AgentProvider:
    name = "test"

    def plan_edit(self, **kwargs):
        return [], "No changes"


def _client(db_session) -> TestClient:
    app = FastAPI()
    for module in MODULES:
        app.include_router(module.router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"m7-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user); db_session.flush()
    return user


def _graph(db_session, owner: User):
    project = Project(owner_id=owner.id, name="M7 AI assistance")
    db_session.add(project); db_session.flush()
    parent = Timeline(project_id=project.id, name="M7 parent", settings_json={}, version=1, is_current=False)
    db_session.add(parent); db_session.flush()
    timeline = Timeline(project_id=project.id, name="M7 current", settings_json={}, version=2, is_current=True, parent_timeline_id=parent.id)
    asset = MediaAsset(project_id=project.id, filename="m7.mp4", storage_key="m7/video.mp4", proxy_key="m7/proxy.mp4", media_type=MediaType.VIDEO, status=MediaStatus.READY, duration_seconds=10)
    db_session.add_all([timeline, asset]); db_session.flush()
    analysis = AIAnalysis(media_asset_id=asset.id, analysis_type=AnalysisType.SPEAKER_STATE, model_name="behavioral_coach_v1", status="completed", result_json={"segments": []})
    profile = AvatarProfile(owner_id=owner.id, project_id=project.id, name="M7 avatar", renderer="unreal_mrq", asset_bundle_key="m7/avatar.zip", rig_mapping_json={})
    db_session.add_all([analysis, profile]); db_session.flush()
    avatar_job = AvatarRenderJob(project_id=project.id, timeline_id=timeline.id, avatar_profile_id=profile.id, source_asset_id=asset.id, source_start=0, source_end=1, provenance_json={})
    agent_run = AgentEditRun(project_id=project.id, source_timeline_id=timeline.id, instruction="test", status=AgentEditStatus.QUEUED, tool_calls_json=[])
    db_session.add_all([avatar_job, agent_run]); db_session.flush()
    return project, parent, timeline, asset, analysis, profile, avatar_job, agent_run


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _profile_body(project: Project, spoofed_id: uuid.UUID):
    return {"user_id": str(spoofed_id), "project_id": str(project.id), "name": "New avatar", "asset_bundle_key": "m7/new.zip", "confirm_asset_license": True, "confirm_subject_consent": True}


def _resource_requests(graph):
    project, parent, timeline, asset, analysis, profile, avatar_job, agent_run = graph
    legacy = {"user_id": str(project.owner_id)}
    return [
        ("post", f"{API}/timelines/{timeline.id}/academic-mode", {**legacy, "glossary": [], "target_programmes": []}),
        ("post", f"{API}/timelines/{timeline.id}/agent-preview", {**legacy, "instruction": "tighten pacing", "timeline_context": {}}),
        ("post", f"{API}/timelines/{timeline.id}/agent-edits", {**legacy, "instruction": "tighten pacing"}),
        ("get", f"{API}/agent-edits/{agent_run.id}?user_id={project.owner_id}", None),
        ("post", f"{API}/avatars/timelines/{timeline.id}/replace-segment", {**legacy, "avatar_profile_id": str(profile.id), "source_asset_id": str(asset.id), "source_start": 0, "source_end": 1, "confirm_subject_consent": True}),
        ("get", f"{API}/avatars/render-jobs/{avatar_job.id}?user_id={project.owner_id}", None),
        ("post", f"{API}/media/{asset.id}/analyze-behavioral-coach", {**legacy, "timeline_id": str(timeline.id)}),
        ("get", f"{API}/media/{asset.id}/behavioral-coach-report?user_id={project.owner_id}", None),
        ("post", f"{API}/timelines/{timeline.id}/apply-behavioral-coach", {**legacy, "analysis_id": str(analysis.id)}),
        ("post", f"{API}/media/{asset.id}/inpaint", {**legacy, "frame_time": .5, "mask_box": {"x": .1, "y": .1, "width": .2, "height": .2}}),
        ("post", f"{API}/timelines/{timeline.id}/analyze-screen-focus", {**legacy, "use_proxy": True}),
        ("post", f"{API}/timelines/{timeline.id}/undo", legacy),
    ]


def _patch_boundaries(monkeypatch, calls: list[str] | None = None) -> None:
    calls = calls if calls is not None else []
    for module, task_name in (
        (academic, "assemble_academic_timeline"), (agent, "apply_edit_instruction"),
        (avatar, "render_avatar_replacement"), (behavioral_coach, "analyze_behavioral_coach"),
        (inpainting, "inpaint_selected_object"), (screen_focus, "analyze_timeline_screen_focus"),
    ):
        monkeypatch.setattr(getattr(module, task_name), "delay", lambda *args, _name=task_name, **kwargs: calls.append(_name) or _QueuedTask())
    monkeypatch.setattr(agent, "get_editing_agent_provider", lambda: calls.append("agent_provider") or _AgentProvider())
    monkeypatch.setattr(agent, "langchain_editing_tools", lambda: [])
    monkeypatch.setattr(behavioral_coach, "apply_coach_markers_to_timeline", lambda *args, **kwargs: calls.append("apply_coach"))


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer malformed"}])
def test_m7_endpoints_reject_anonymous_and_invalid_jwts(db_session, monkeypatch, headers):
    _patch_boundaries(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    requests = [("post", f"{API}/avatars/profiles", _profile_body(graph[0], owner.id)), *_resource_requests(graph)]
    for method, url, body in requests:
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 401, (method, url, response.text)


def test_m7_wrong_user_and_legacy_spoof_cannot_mutate_or_trigger_side_effects(db_session, monkeypatch):
    calls: list[str] = []; _patch_boundaries(monkeypatch, calls)
    owner, attacker = _user(db_session), _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    original = copy.deepcopy(graph[2].settings_json)
    for method, url, body in _resource_requests(graph):
        kwargs = {"headers": _auth(attacker)}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code == 403, (method, url, response.text)
    assert calls == []
    db_session.refresh(graph[2]); assert graph[2].settings_json == original


def test_m7_avatar_profile_is_attributed_to_token_not_spoofed_owner(db_session, monkeypatch):
    _patch_boundaries(monkeypatch); owner, attacker = _user(db_session), _user(db_session)
    project = Project(owner_id=attacker.id, name="attacker project"); db_session.add(project); db_session.flush()
    client = _client(db_session)
    response = client.post(f"{API}/avatars/profiles", json=_profile_body(project, owner.id), headers=_auth(attacker))
    assert response.status_code == 201, response.text
    created = db_session.get(AvatarProfile, uuid.UUID(response.json()["id"]))
    assert created.owner_id == attacker.id and created.owner_id != owner.id


def test_m7_rightful_owner_can_use_every_migrated_resource_endpoint(db_session, monkeypatch):
    _patch_boundaries(monkeypatch); owner = _user(db_session); graph = _graph(db_session, owner); client = _client(db_session)
    for method, url, body in _resource_requests(graph):
        kwargs = {"headers": _auth(owner)}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in {200, 201, 202, 204}, (method, url, response.status_code, response.text)
