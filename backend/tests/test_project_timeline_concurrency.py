"""Two tabs creating the first timeline at once, against a real PostgreSQL.

Review caught this: `create_project_timeline` read `MAX(version)`, demoted the
current rows, and inserted a new current one, with nothing serialising two
requests. Both could read zero, both update zero rows, and both commit
version 1 with `is_current` true. Neither invariant has a unique constraint, so
the database would not have rejected the second row either.

The fix is a `SELECT ... FOR UPDATE` on the owned `projects` row, taken before
`MAX(version)` is read. These tests are the evidence it works, and the control
below is the evidence they are worth anything.

## Why these do not use the `db_session` fixture

That fixture hands every test one connection inside a transaction it rolls
back at the end. Nothing concurrent can happen on it: two "requests" sharing a
connection are serialised by the connection itself, and a race that cannot
occur cannot be tested. So these open their own sessions, commit for real, and
clean up in a `finally`.

## Why the route function is called directly

The handler is an ordinary synchronous function whose dependencies are plain
parameters. Calling it with a real `Session` exercises the locking, the version
arithmetic and the demote/promote exactly as a request would, without a
TestClient in each thread. Nothing is inserted behind the route's back: every
timeline in these tests is created by the route under test.

Skipped unless the database is PostgreSQL: `SELECT ... FOR UPDATE` is a no-op
on SQLite, so a green run there would prove nothing.
"""
from __future__ import annotations

import importlib.util
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import engine
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, Timeline, User
from app.schemas.project_timeline import ProjectTimelineCreateRequest


def _load():
    path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "project_timelines.py"
    spec = importlib.util.spec_from_file_location("_vantacut_project_timelines_concurrency", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timelines_api = _load()

Sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)

requires_postgres = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="row locks are a no-op outside PostgreSQL, so a pass would prove nothing",
)


@pytest.fixture()
def owned_project():
    """A committed user, project and READY asset, removed afterwards.

    Committed rather than held in a transaction because the point is for two
    other connections to see them.
    """
    setup: Session = Sessions()
    user = User(email=f"concurrency-{uuid.uuid4().hex[:16]}@example.com", is_active=True)
    setup.add(user)
    setup.flush()
    project = Project(owner_id=user.id, name="Concurrent timeline creation")
    setup.add(project)
    setup.flush()
    asset = MediaAsset(
        project_id=project.id,
        filename="clip.mp4",
        storage_key=f"{uuid.uuid4()}-clip.mp4",
        media_type=MediaType.VIDEO,
        status=MediaStatus.READY,
        duration_seconds=9.0,
    )
    setup.add(asset)
    setup.commit()
    identifiers = (user.id, project.id, asset.id)
    setup.close()

    try:
        yield identifiers
    finally:
        cleanup: Session = Sessions()
        try:
            cleanup.query(Timeline).filter(Timeline.project_id == identifiers[1]).delete(
                synchronize_session=False
            )
            cleanup.query(MediaAsset).filter(MediaAsset.project_id == identifiers[1]).delete(
                synchronize_session=False
            )
            cleanup.query(Project).filter(Project.id == identifiers[1]).delete(
                synchronize_session=False
            )
            cleanup.query(User).filter(User.id == identifiers[0]).delete(synchronize_session=False)
            cleanup.commit()
        finally:
            cleanup.close()


def _timeline_rows(project_id) -> list[Timeline]:
    session: Session = Sessions()
    try:
        return list(
            session.scalars(
                select(Timeline)
                .where(Timeline.project_id == project_id)
                .order_by(Timeline.version)
            ).all()
        )
    finally:
        session.close()


@requires_postgres
def test_two_concurrent_first_timelines_get_distinct_versions(owned_project) -> None:
    """The regression review asked for.

    Both requests arrive together, and the lock makes the second wait until the
    first has committed, so it reads version 1 rather than zero.
    """
    user_id, project_id, asset_id = owned_project
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, object]] = []
    lock = threading.Lock()

    def attempt() -> None:
        session: Session = Sessions()
        try:
            caller = session.get(User, user_id)
            # Open the transaction before the barrier so what races is the
            # route, not two connection checkouts.
            session.execute(select(func.now()))
            barrier.wait(timeout=30)
            created = timelines_api.create_project_timeline(
                project_id=project_id,
                payload=ProjectTimelineCreateRequest(source_asset_id=asset_id),
                current_user=caller,
                db=session,
            )
            with lock:
                outcomes.append(("created", created.version))
        except Exception as error:  # noqa: BLE001 - recorded and asserted on below
            with lock:
                outcomes.append(("error", repr(error)))
        finally:
            session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert [kind for kind, _ in outcomes] == ["created", "created"], outcomes
    assert sorted(value for _, value in outcomes) == [1, 2], outcomes

    rows = _timeline_rows(project_id)
    assert [row.version for row in rows] == [1, 2]
    assert len({row.version for row in rows}) == 2, "two rows shared a version number"


@requires_postgres
def test_exactly_one_timeline_is_current_after_a_concurrent_pair(owned_project) -> None:
    """The second invariant, and the one with the worse failure.

    `agent.py`, `academic.py` and `lecturas.py` all refuse a timeline that is
    not current. With two current rows, which one they accept depends on row
    order.
    """
    user_id, project_id, asset_id = owned_project
    barrier = threading.Barrier(2)

    def attempt() -> None:
        session: Session = Sessions()
        try:
            caller = session.get(User, user_id)
            session.execute(select(func.now()))
            barrier.wait(timeout=30)
            timelines_api.create_project_timeline(
                project_id=project_id,
                payload=ProjectTimelineCreateRequest(source_asset_id=asset_id),
                current_user=caller,
                db=session,
            )
        finally:
            session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    rows = _timeline_rows(project_id)
    current = [row for row in rows if row.is_current]
    assert len(rows) == 2, [row.version for row in rows]
    assert len(current) == 1, f"{len(current)} timelines are current"
    # And it is the newer one: the later writer demoted the earlier.
    assert current[0].version == max(row.version for row in rows)


@requires_postgres
def test_without_the_lock_the_same_pair_collides(owned_project) -> None:
    """The control, and the reason the two tests above mean anything.

    This is the route's own read-then-write sequence with the lock left out,
    on the same schedule against the same server. If it stopped colliding, the
    locked tests would be passing for some reason other than the lock, and
    this failing is how that gets noticed.

    Two barriers make the interleaving certain rather than likely: both
    transactions read `MAX(version)` before either writes, which is exactly
    what the row lock makes impossible.
    """
    _user_id, project_id, asset_id = owned_project
    read_done = threading.Barrier(2)
    write_ready = threading.Barrier(2)

    def unlocked_attempt() -> None:
        session: Session = Sessions()
        try:
            # No FOR UPDATE here: that omission is the whole point.
            project = session.scalar(select(Project).where(Project.id == project_id))
            read_done.wait(timeout=30)
            next_version = int(
                session.scalar(
                    select(func.max(Timeline.version)).where(Timeline.project_id == project.id)
                )
                or 0
            ) + 1
            write_ready.wait(timeout=30)
            session.query(Timeline).filter(
                Timeline.project_id == project.id, Timeline.is_current.is_(True)
            ).update({Timeline.is_current: False}, synchronize_session=False)
            session.add(
                Timeline(
                    project_id=project.id,
                    name="unlocked",
                    version=next_version,
                    is_current=True,
                    settings_json={
                        "confirmed_timeline": {
                            "source_asset_id": str(asset_id),
                            "segments": [
                                {"source_start": 0.0, "source_end": 9.0, "action": "keep"}
                            ],
                        }
                    },
                )
            )
            session.commit()
        finally:
            session.close()

    threads = [threading.Thread(target=unlocked_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    rows = _timeline_rows(project_id)
    versions = [row.version for row in rows]
    current = [row for row in rows if row.is_current]

    assert versions == [1, 1], f"the unlocked race did not collide on version: {versions}"
    assert len(current) == 2, (
        f"the unlocked race left {len(current)} current timelines, so the locked "
        f"tests above are not evidence of anything"
    )
