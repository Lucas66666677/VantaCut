"""Regression tests for migration
0032_fix_distributed_render_and_license_timestamp_defaults.

Background (full detail in that migration's own docstring): five tables —
template_licenses (0024_add_template_marketplace.py's `_timestamps()`
helper) and distributed_render_batches / distributed_render_chunks /
distributed_render_assignments / compute_credit_ledger
(0028_add_distributed_compute.py's `_base_columns()` helper) — share the
identical missing-`server_default` bug already fixed for review_participants
(0030) and marketplace_templates/creator_connect_accounts/compute_nodes
(0031). Every one of the five already has a real production call site that
constructs the model without explicit timestamps: app/api/v1/marketplace.py's
`create_checkout` (TemplateLicense) and
app/services/distributed_compute.py's `create_batch` (DistributedRenderBatch,
DistributedRenderChunk), `assign_next_chunk` (DistributedRenderAssignment),
and `settle_credits` (ComputeCreditLedger).

Like test_review_participant_schema.py and
test_marketplace_and_compute_schema.py, these tests query
`information_schema` against the CI-migrated database (a real
`alembic upgrade head` run), not a `Base.metadata.create_all()` fallback,
since only the former exercises the previously-broken migration path.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.db.session import engine
from app.models.entities import (
    ComputeCreditLedger, ComputeNode, ComputeNodeStatus, DistributedBatchStatus, DistributedRenderAssignment,
    DistributedRenderBatch, DistributedRenderChunk, MarketplaceTemplate, MarketplaceTemplateStatus, Project,
    RenderJob, RenderStatus, Template, TemplateLicense, Timeline, User,
)


def _column_default(table: str, column: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar_one_or_none()


def _assert_has_now_default(table: str, column: str) -> None:
    default = _column_default(table, column)
    assert default is not None, (
        f"{table}.{column} has no server_default in the actual database — "
        "migration 0032_fix_distributed_render_and_license_timestamp_defaults "
        "did not apply, or `alembic upgrade head` was not run before this "
        "test suite."
    )
    assert "now()" in default.lower()


def _make_user(db_session) -> User:
    user = User(email=f"schema-fix2-{uuid.uuid4().hex[:12]}@example.com", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


def _make_project(db_session, owner: User) -> Project:
    project = Project(owner_id=owner.id, name="Schema fix 2 regression test project")
    db_session.add(project)
    db_session.flush()
    return project


def _make_timeline(db_session, project: Project) -> Timeline:
    timeline = Timeline(project_id=project.id, name="Schema fix 2 regression test timeline")
    db_session.add(timeline)
    db_session.flush()
    return timeline


def _make_render_job(db_session, project: Project, timeline: Timeline) -> RenderJob:
    job = RenderJob(project_id=project.id, timeline_id=timeline.id, status=RenderStatus.QUEUED)
    db_session.add(job)
    db_session.flush()
    return job


def _make_compute_node(db_session, owner: User) -> ComputeNode:
    node = ComputeNode(
        owner_id=owner.id,
        label="Schema fix 2 regression test node",
        public_key=uuid.uuid4().hex + uuid.uuid4().hex,
        node_kind="browser",
        status=ComputeNodeStatus.ACTIVE,
        capabilities_json={},
        consent_json={"explicit_opt_in": True},
    )
    db_session.add(node)
    db_session.flush()
    return node


# --- server_default existence, one test per table ---------------------------

def test_template_licenses_timestamps_have_server_default_in_db():
    _assert_has_now_default("template_licenses", "created_at")
    _assert_has_now_default("template_licenses", "updated_at")


def test_distributed_render_batches_timestamps_have_server_default_in_db():
    _assert_has_now_default("distributed_render_batches", "created_at")
    _assert_has_now_default("distributed_render_batches", "updated_at")


def test_distributed_render_chunks_timestamps_have_server_default_in_db():
    _assert_has_now_default("distributed_render_chunks", "created_at")
    _assert_has_now_default("distributed_render_chunks", "updated_at")


def test_distributed_render_assignments_timestamps_have_server_default_in_db():
    _assert_has_now_default("distributed_render_assignments", "created_at")
    _assert_has_now_default("distributed_render_assignments", "updated_at")


def test_compute_credit_ledger_timestamps_have_server_default_in_db():
    _assert_has_now_default("compute_credit_ledger", "created_at")
    _assert_has_now_default("compute_credit_ledger", "updated_at")


# --- production-style insert without explicit timestamps succeeds -----------

def test_template_license_insert_without_explicit_timestamps_succeeds(db_session):
    """Mirrors app/api/v1/marketplace.py's create_checkout: constructs
    TemplateLicense(...) without explicit timestamps."""
    creator = _make_user(db_session)
    buyer = _make_user(db_session)
    project = _make_project(db_session, buyer)
    template_project = _make_project(db_session, creator)
    template = Template(project_id=template_project.id, name="Schema fix 2 test template", structure_json={})
    db_session.add(template)
    db_session.flush()
    listing = MarketplaceTemplate(
        template_id=template.id,
        creator_id=creator.id,
        slug=f"schema-fix2-{uuid.uuid4().hex[:12]}",
        title="Schema fix 2 test listing",
        status=MarketplaceTemplateStatus.PUBLISHED.value,
        price_cents=1000,
        currency="usd",
        encrypted_payload="not-a-real-payload",
        payload_sha256="0" * 64,
        safe_preview_json={},
    )
    db_session.add(listing)
    db_session.flush()

    license_row = TemplateLicense(
        marketplace_template_id=listing.id,
        buyer_id=buyer.id,
        project_id=project.id,
        gross_amount_cents=listing.price_cents,
        currency=listing.currency,
        creator_share_cents=700,
        platform_share_cents=300,
        transfer_group=f"tmpl_license_{uuid.uuid4().hex}",
        template_payload_sha256=listing.payload_sha256,
        blackbox_render_only=True,
    )
    db_session.add(license_row)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    db_session.refresh(license_row)
    assert license_row.created_at is not None
    assert license_row.updated_at is not None


def test_distributed_render_batch_and_chunk_insert_without_explicit_timestamps_succeeds(db_session):
    """Mirrors app/services/distributed_compute.py's create_batch, which
    constructs both DistributedRenderBatch and DistributedRenderChunk
    without explicit timestamps."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    render_job = _make_render_job(db_session, project, timeline)

    batch = DistributedRenderBatch(
        render_job_id=render_job.id,
        project_id=project.id,
        owner_id=owner.id,
        status=DistributedBatchStatus.DISPATCHING,
        chunk_seconds=5,
        replication_factor=2,
        manifest_json={"schema": "test"},
        manifest_sha256="0" * 64,
    )
    db_session.add(batch)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    chunk = DistributedRenderChunk(
        batch_id=batch.id,
        chunk_index=0,
        output_start_seconds=0,
        output_end_seconds=5,
        manifest_json={"schema": "test-chunk"},
        manifest_sha256="1" * 64,
        required_replicas=2,
    )
    db_session.add(chunk)
    db_session.flush()

    db_session.refresh(batch)
    db_session.refresh(chunk)
    assert batch.created_at is not None
    assert batch.updated_at is not None
    assert chunk.created_at is not None
    assert chunk.updated_at is not None
    return batch, chunk


def test_distributed_render_assignment_insert_without_explicit_timestamps_succeeds(db_session):
    """Mirrors app/services/distributed_compute.py's assign_next_chunk,
    which constructs DistributedRenderAssignment without explicit
    timestamps."""
    owner = _make_user(db_session)
    project = _make_project(db_session, owner)
    timeline = _make_timeline(db_session, project)
    render_job = _make_render_job(db_session, project, timeline)
    batch = DistributedRenderBatch(
        render_job_id=render_job.id, project_id=project.id, owner_id=owner.id,
        status=DistributedBatchStatus.DISPATCHING, manifest_json={}, manifest_sha256="0" * 64,
    )
    db_session.add(batch)
    db_session.flush()
    chunk = DistributedRenderChunk(
        batch_id=batch.id, chunk_index=0, output_start_seconds=0, output_end_seconds=5,
        manifest_json={}, manifest_sha256="1" * 64,
    )
    db_session.add(chunk)
    db_session.flush()
    node = _make_compute_node(db_session, owner)

    assignment = DistributedRenderAssignment(
        chunk_id=chunk.id,
        node_id=node.id,
        ticket_nonce=uuid.uuid4().hex,
        ticket_sha256="2" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(assignment)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    db_session.refresh(assignment)
    assert assignment.created_at is not None
    assert assignment.updated_at is not None


def test_compute_credit_ledger_insert_without_explicit_timestamps_succeeds(db_session):
    """Mirrors app/services/distributed_compute.py's settle_credits, which
    constructs ComputeCreditLedger without explicit timestamps."""
    owner = _make_user(db_session)
    node = _make_compute_node(db_session, owner)

    entry = ComputeCreditLedger(
        user_id=owner.id,
        node_id=node.id,
        assignment_id=None,
        amount=10,
        event_type="verified_chunk",
        idempotency_key=f"schema-fix2-{uuid.uuid4().hex}",
        metadata_json={"checksum": "test"},
    )
    db_session.add(entry)
    db_session.flush()  # would previously raise sqlalchemy.exc.IntegrityError (NotNullViolation)

    db_session.refresh(entry)
    assert entry.created_at is not None
    assert entry.updated_at is not None
