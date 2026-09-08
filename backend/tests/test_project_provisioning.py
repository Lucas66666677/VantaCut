"""A signed-in person can obtain the project every later leg requires.

`tests/preflight/test_public_journey.py` recorded the gap statically: 195
routes, 120 of them POST, none constructing a `Project`, so a person who
registered could not upload. These are the routes that close it, and the checks
here are about the two things a new authenticated write surface can get wrong --
whose project it creates, and whose projects it shows.

Ownership is taken from the verified token and never from the request. That is
not a formality in this codebase: routes here still accept a client-supplied
`user_id` (see `artifacts/service-readiness/vantacut-auth-route-map.md`), and a
new route repeating that pattern would let anyone create a project inside
someone else's account -- and with it an upload target pointed at their
storage.

Real JWTs against a real database session, the same way the M11 media ownership
tests work. No network, no storage, no queue.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.session import get_db
from app.models.entities import Project, User

API = "/api/v1"


def _load():
    """Load `app/api/v1/projects.py` directly, bypassing the package `__init__`.

    A plain `from app.api.v1.projects import router` first executes
    `app/api/__init__.py`, which eagerly imports all ~75 v1 routers -- several
    of which reach `torch` and other packages this CI slice deliberately does
    not install. Same reasoning as `test_m11_media_ownership_identity.py`.
    """
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "projects.py"
    spec = importlib.util.spec_from_file_location("_vantacut_projects_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


projects_router = _load().router


def _client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(projects_router, prefix=API)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _user(db_session) -> User:
    user = User(email=f"projects-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_creating_a_project_requires_a_session(db_session) -> None:
    """Anonymous creation would be an open write into someone's storage path."""

    assert _client(db_session).post(f"{API}/projects", json={}).status_code == 401


def test_listing_projects_requires_a_session(db_session) -> None:
    assert _client(db_session).get(f"{API}/projects").status_code == 401


def test_a_signed_in_person_can_create_a_project(db_session) -> None:
    """The leg that did not exist. An empty body is valid on purpose.

    The studio creates a workspace before the visitor has named anything, so
    requiring a name would just push a placeholder into the client.
    """

    owner = _user(db_session)
    response = _client(db_session).post(f"{API}/projects", headers=_auth(owner), json={})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lifecycle_state"] == "active"
    assert body["name"]

    stored = db_session.get(Project, uuid.UUID(body["id"]))
    assert stored is not None
    assert stored.owner_id == owner.id


def test_the_owner_comes_from_the_token_not_the_request(db_session) -> None:
    """The check this route exists to get right.

    A body field naming another user must not be able to place a project in
    their account. Extra fields are ignored by the schema, so this asserts the
    outcome rather than the rejection: whatever the body says, the row belongs
    to the caller.
    """

    caller = _user(db_session)
    victim = _user(db_session)

    response = _client(db_session).post(
        f"{API}/projects",
        headers=_auth(caller),
        json={"name": "spoof attempt", "owner_id": str(victim.id), "user_id": str(victim.id)},
    )

    assert response.status_code == 201, response.text
    stored = db_session.get(Project, uuid.UUID(response.json()["id"]))
    assert stored.owner_id == caller.id
    assert stored.owner_id != victim.id


def test_the_listing_shows_only_the_callers_projects(db_session) -> None:
    """Another person's project is invisible, not forbidden.

    Filtering in the query rather than checking afterwards also means an
    unauthorised caller learns nothing about what exists.
    """

    caller = _user(db_session)
    other = _user(db_session)
    client = _client(db_session)

    mine = client.post(f"{API}/projects", headers=_auth(caller), json={"name": "mine"})
    theirs = client.post(f"{API}/projects", headers=_auth(other), json={"name": "theirs"})
    assert mine.status_code == 201 and theirs.status_code == 201

    listed = client.get(f"{API}/projects", headers=_auth(caller))
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()}
    assert mine.json()["id"] in ids
    assert theirs.json()["id"] not in ids


def test_the_response_carries_no_owner_identifier(db_session) -> None:
    """Nothing here needs to publish who owns what.

    Every route that returns this is already scoped to the caller, so an
    `owner_id` in the payload would be a user identifier travelling for no
    reason.
    """

    owner = _user(db_session)
    body = _client(db_session).post(f"{API}/projects", headers=_auth(owner), json={}).json()

    assert "owner_id" not in body
    assert "user_id" not in body
    assert str(owner.id) not in str(body)


def test_a_long_name_is_rejected_rather_than_truncated(db_session) -> None:
    """The column is 200 characters; a longer value must be a 422, not a 500."""

    owner = _user(db_session)
    response = _client(db_session).post(
        f"{API}/projects", headers=_auth(owner), json={"name": "x" * 400}
    )
    assert response.status_code == 422


def test_the_listing_is_bounded(db_session) -> None:
    """A first call from the studio must not be able to ask for everything."""

    owner = _user(db_session)
    client = _client(db_session)
    assert client.get(f"{API}/projects?limit=1000", headers=_auth(owner)).status_code == 422
    assert client.get(f"{API}/projects?limit=0", headers=_auth(owner)).status_code == 422
