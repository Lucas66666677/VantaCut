"""A signed-in person can turn an uploaded asset into an exportable timeline.

`test_project_provisioning.py` closed the leg before this one: nothing created
a `Project`, so a person who registered could not upload. Upload then worked
and stopped. Every export route -- `POST /timelines/{id}/render`, the
omnichannel matrix, the download URL -- is keyed by a timeline id, and no route
in `app/api/v1` produced one: every `Timeline(...)` in the backend is inside a
Celery task or an AI pipeline, or clones a timeline that already exists.

The checks here are about the four things this new authenticated write surface
can get wrong: whose project it writes into, which asset it will accept, what
it does with an asset that is not ready, and whether the timeline it produces
is one the render route will actually accept.

Ownership comes from the verified token and never from the request. Routes in
this repository still accept a client-supplied `user_id` (see
`artifacts/service-readiness/vantacut-auth-route-map.md`); a new route
repeating that would let anyone create a timeline -- and request a render --
inside someone else's project.

Real JWTs against a real database session, the same way the M11 media
ownership tests work. No network, no storage, no queue.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, Timeline, User

API = "/api/v1"


def _load():
    """Load `app/api/v1/project_timelines.py` directly, bypassing the package
    `__init__`, which eagerly imports all ~75 v1 routers -- several of which
    reach `torch` and other packages this CI slice does not install. Same
    reasoning as `test_project_provisioning.py`.
    """
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "project_timelines.py"
    spec = importlib.util.spec_from_file_location("_vantacut_project_timelines_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timelines_router = _load().router


def _client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(timelines_router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"timelines-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Timeline provisioning")
    db_session.add(project)
    db_session.flush()
    return project


def _asset(
    db_session,
    project: Project,
    *,
    status: MediaStatus = MediaStatus.READY,
    duration: float | None = 12.5,
    media_type: MediaType = MediaType.VIDEO,
) -> MediaAsset:
    asset = MediaAsset(
        project_id=project.id,
        filename="clip.mp4",
        storage_key=f"{uuid.uuid4()}-clip.mp4",
        media_type=media_type,
        status=status,
        duration_seconds=duration,
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# --------------------------------------------------------------------------- #
# Who it writes for
# --------------------------------------------------------------------------- #


def test_creating_a_timeline_requires_a_session(db_session) -> None:
    project = _project(db_session, _user(db_session))
    response = _client(db_session).post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(uuid.uuid4())},
    )
    assert response.status_code == 401


def test_a_stranger_cannot_create_a_timeline_in_someone_elses_project(db_session) -> None:
    """The check that matters most: a timeline is a render request waiting to
    happen, and a render spends the project owner's credits."""
    owner, stranger = _user(db_session), _user(db_session)
    project = _project(db_session, owner)
    asset = _asset(db_session, project)

    response = _client(db_session).post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id)},
        headers=_auth(stranger),
    )

    assert response.status_code == 404, response.text
    assert db_session.query(Timeline).filter(Timeline.project_id == project.id).count() == 0


def test_an_asset_from_another_project_cannot_be_pulled_in(db_session) -> None:
    """Even the owner of both must not cross projects.

    The asset is looked up inside the project rather than globally, so a
    timeline cannot reference media the project does not hold -- which is what
    `job.project.owner_id` authorisation on download later assumes.
    """
    owner = _user(db_session)
    project, other_project = _project(db_session, owner), _project(db_session, owner)
    foreign_asset = _asset(db_session, other_project)

    response = _client(db_session).post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(foreign_asset.id)},
        headers=_auth(owner),
    )

    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# What it refuses, and why
# --------------------------------------------------------------------------- #


def test_an_asset_still_processing_is_a_retry_not_a_rejection(db_session) -> None:
    """409, because nothing about the request is wrong.

    `duration_seconds` is NULL until `process_new_media` has probed the file.
    The client should wait, not change anything, and the status is named so it
    knows which.
    """
    owner = _user(db_session)
    project = _project(db_session, owner)
    asset = _asset(db_session, project, status=MediaStatus.PROCESSING, duration=None)

    response = _client(db_session).post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id)},
        headers=_auth(owner),
    )

    assert response.status_code == 409, response.text
    assert "processing" in response.json()["detail"]


def test_an_image_cannot_be_the_source_of_a_timed_cut(db_session) -> None:
    owner = _user(db_session)
    project = _project(db_session, owner)
    asset = _asset(db_session, project, media_type=MediaType.IMAGE, duration=None)

    response = _client(db_session).post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id)},
        headers=_auth(owner),
    )

    assert response.status_code == 422, response.text


def test_a_ready_asset_with_no_probed_duration_is_refused(db_session) -> None:
    """READY but unmeasured should not become a silently wrong export.

    Guessing a length here would produce an artifact that looks fine and is the
    wrong duration, which is worse than a refusal.
    """
    owner = _user(db_session)
    project = _project(db_session, owner)
    asset = _asset(db_session, project, status=MediaStatus.READY, duration=None)

    response = _client(db_session).post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id)},
        headers=_auth(owner),
    )

    assert response.status_code == 422, response.text
    assert "duration" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# What it produces
# --------------------------------------------------------------------------- #


def test_the_owner_gets_a_timeline_the_render_route_would_accept(db_session) -> None:
    """The point of the whole route.

    Asserting that a row was created would pass for a timeline the renderer
    rejects with `400 Confirmed timeline has no keep segments`. So this
    reproduces `_render_duration` against the stored document: a positive
    duration is what separates "a timeline exists" from "an export can be
    requested".
    """
    owner = _user(db_session)
    project = _project(db_session, owner)
    asset = _asset(db_session, project, duration=12.5)

    response = _client(db_session).post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id), "name": "First cut"},
        headers=_auth(owner),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "First cut"
    assert body["is_current"] is True
    assert body["version"] == 1

    timeline = db_session.get(Timeline, uuid.UUID(body["id"]))
    document = timeline.settings_json["confirmed_timeline"]
    assert document["source_asset_id"] == str(asset.id)

    duration = sum(
        float(segment["source_end"]) - float(segment["source_start"])
        for segment in document["segments"]
        if segment.get("action", "keep") == "keep"
    )
    assert duration > 0, "the render route refuses a timeline with no keep segments"
    assert duration == 12.5


def test_a_second_timeline_takes_over_as_current(db_session) -> None:
    """`agent.py`, `academic.py` and `lecturas.py` all refuse a timeline that
    is not current, so leaving two behind would make which one they accept
    depend on row order."""
    owner = _user(db_session)
    project = _project(db_session, owner)
    asset = _asset(db_session, project)
    client = _client(db_session)

    first = client.post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id)},
        headers=_auth(owner),
    ).json()
    second = client.post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id)},
        headers=_auth(owner),
    ).json()

    assert second["version"] == 2
    current = (
        db_session.query(Timeline)
        .filter(Timeline.project_id == project.id, Timeline.is_current.is_(True))
        .all()
    )
    assert [str(item.id) for item in current] == [second["id"]]
    assert db_session.get(Timeline, uuid.UUID(first["id"])).is_current is False


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


def test_the_listing_shows_only_your_own_project(db_session) -> None:
    owner, stranger = _user(db_session), _user(db_session)
    project = _project(db_session, owner)
    asset = _asset(db_session, project)
    client = _client(db_session)
    client.post(
        f"{API}/projects/{project.id}/timelines",
        json={"source_asset_id": str(asset.id)},
        headers=_auth(owner),
    )

    assert client.get(f"{API}/projects/{project.id}/timelines", headers=_auth(stranger)).status_code == 404

    mine = client.get(f"{API}/projects/{project.id}/timelines", headers=_auth(owner))
    assert mine.status_code == 200, mine.text
    assert len(mine.json()) == 1
