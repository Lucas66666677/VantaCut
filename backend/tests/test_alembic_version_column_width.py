"""Regression test for the alembic_version.version_num width bug.

Alembic 1.14's MigrationContext hardcodes this column as VARCHAR(32) (see
version_table_impl() in alembic/ddl/impl.py) with no supported
context.configure() override — a "version_table_column_size" kwarg was
tried first and confirmed (by reading the installed Alembic's source) to
be silently ignored. migrations/env.py now widens the column directly via
_widen_alembic_version_column() before any migration runs.

This test proves the widened column actually made it to head, so a future
Alembic/SQLAlchemy upgrade, or an env.py edit that accidentally drops the
widening step, is caught here instead of surfacing later as an opaque
StringDataRightTruncation the next time a >32-char revision id is written.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text

from app.db.session import engine


def _version_num_width() -> int | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = 'alembic_version' "
                "AND column_name = 'version_num'"
            )
        ).scalar_one_or_none()


def test_alembic_version_column_is_wide_enough():
    width = _version_num_width()
    assert width is not None, (
        "alembic_version.version_num not found — was `alembic upgrade head` "
        "run before this test suite?"
    )
    assert width >= 64, (
        f"alembic_version.version_num is only VARCHAR({width}); expected "
        ">= 64. See migrations/env.py's _widen_alembic_version_column()."
    )


def test_alembic_head_revision_id_fits_in_column():
    """Prove the actual longest revision id used in this repo fits, rather
    than just checking an arbitrary threshold that could itself go stale."""
    versions_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    revision_pattern = re.compile(r'^revision\s*=\s*"([^"]+)"', re.MULTILINE)

    longest = 0
    for migration_file in versions_dir.glob("*.py"):
        match = revision_pattern.search(migration_file.read_text())
        if match:
            longest = max(longest, len(match.group(1)))

    assert longest > 32, (
        "Expected at least one revision id longer than 32 chars in this "
        "repo (that's the bug this test guards against) — if that's no "
        "longer true, this assertion (not the column-width one below) "
        "should be revisited."
    )

    width = _version_num_width()
    assert width is not None and width >= longest, (
        f"Longest revision id in migrations/versions is {longest} chars, "
        f"but alembic_version.version_num is only VARCHAR({width})."
    )
