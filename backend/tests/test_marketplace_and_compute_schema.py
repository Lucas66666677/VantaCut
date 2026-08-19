"""Regression tests for migration
0031_fix_marketplace_and_compute_timestamp_defaults.

Background (full detail in that migration's own docstring): migrations
0024_add_template_marketplace.py and 0028_add_distributed_compute.py each
created their tables' `created_at`/`updated_at` columns as `nullable=False`
with no `server_default`, even though the corresponding ORM models declare
one via `TimestampMixin`. This is the same bug class fixed for
review_participants in 0030 (see test_review_participant_schema.py), now
surfaced for marketplace_templates, creator_connect_accounts, and
compute_nodes — evidenced by a real CI failure this batch, when
backend/tests/test_marketplace_dashboard.py's `_make_marketplace_template`
and `test_owner_sees_own_dashboard` hit a real `NotNullViolation` against a
live Postgres service container.

Like test_review_participant_schema.py, these tests query
`information_schema` against the CI-migrated database (a real
`alembic upgrade head` run), not a `Base.metadata.create_all()` fallback,
since only the former exercises the previously-broken migration path.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.session import engine
from app.models.entities import ComputeNode, ComputeNodeStatus, CreatorConnectAccount, MarketplaceTemplate, MarketplaceTemplateStatus, Project, Template, User


def _column_default(table: str, column: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar_one_or_none()


def _make_user(db_session) -> User:
    user = User(email=f"schema-fix-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _assert_has_now_default(table: str, column: str) -> None:
    default = _column_default(table, column)
    assert default is not None, (
        f"{table}.{column} has no server_default in the actual database — "
        "migration 0031_fix_marketplace_and_compute_timestamp_defaults did "
        "not apply, or `alembic upgrade head` was not run before this test "
        "suite."
    )
    assert "now()" in default.lower()


def test_marketplace_templates_timestamps_have_server_default_in_db():
    _assert_has_now_default("marketplace_templates", "created_at")
    _assert_has_now_default("marketplace_templates", "updated_at")


def test_creator_connect_accounts_timestamps_have_server_default_in_db():
    _assert_has_now_default("creator_connect_accounts", "created_at")
    _assert_has_now_default("creator_connect_accounts", "updated_at")


def test_compute_nodes_timestamps_have_server_default_in_db():
    _assert_has_now_default("compute_nodes", "created_at")
    _assert_has_now_default("compute_nodes", "updated_at")


def test_marketplace_template_insert_without_explicit_timestamps_succeeds(db_session):
    """Proves the real production pattern used by app/api/v1/marketplace.py's
    publish_template (constructs MarketplaceTemplate(...) without explicit
    timestamps) now succeeds, instead of raising NotNullViolation. This is
    the exact insert this batch's own test_marketplace_dashboard.py already
    relies on — this test isolates the schema fix from the auth-route
    behavior those other tests exercise."""
    creator = _make_user(db_session)
    project = Project(owner_id=creator.id, name="Schema fix regression test project")
    db_session.add(project)
    db_session.flush()
    template = Template(project_id=project.id, name="Schema fix regression test template", structure_json={})
    db_session.add(template)
    db_session.flush()

    listing = MarketplaceTemplate(
        template_id=template.id,
        creator_id=creator.id,
        slug=f"schema-fix-{uuid.uuid4().hex[:12]}",
        title="Schema fix regression test listing",
        status=MarketplaceTemplateStatus.PUBLISHED.value,
        price_cents=1000,
        currency="usd",
        encrypted_payload="not-a-real-payload",
        payload_sha256="0" * 64,
        safe_preview_json={},
    )
    db_session.add(listing)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    db_session.refresh(listing)
    assert listing.created_at is not None
    assert listing.updated_at is not None


def test_creator_connect_account_insert_without_explicit_timestamps_succeeds(db_session):
    creator = _make_user(db_session)

    account = CreatorConnectAccount(
        creator_id=creator.id,
        stripe_account_id=f"acct_{uuid.uuid4().hex[:16]}",
        details_submitted=True,
        charges_enabled=True,
        payouts_enabled=True,
    )
    db_session.add(account)
    db_session.flush()

    db_session.refresh(account)
    assert account.created_at is not None
    assert account.updated_at is not None


def test_compute_node_insert_without_explicit_timestamps_succeeds(db_session):
    """Proves the real production pattern used by this batch's own
    app/api/v1/distributed_compute.py's enroll_compute_node (constructs
    ComputeNode(...) without explicit created_at/updated_at) now succeeds."""
    owner = _make_user(db_session)

    node = ComputeNode(
        owner_id=owner.id,
        label="Schema fix regression test node",
        public_key=uuid.uuid4().hex + uuid.uuid4().hex,
        node_kind="browser",
        status=ComputeNodeStatus.ACTIVE,
        capabilities_json={},
        consent_json={"explicit_opt_in": True},
    )
    db_session.add(node)
    db_session.flush()

    db_session.refresh(node)
    assert node.created_at is not None
    assert node.updated_at is not None
