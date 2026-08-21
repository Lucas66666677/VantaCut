"""Regression for server-managed timestamps used by M7 direct inserts."""
from sqlalchemy import text


def test_ai_assistance_timestamps_have_database_defaults(db_session):
    rows = db_session.execute(text("""
        SELECT table_name, column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ('agent_edit_runs', 'avatar_profiles', 'avatar_render_jobs')
          AND column_name IN ('created_at', 'updated_at')
    """)).mappings().all()
    assert {(row["table_name"], row["column_name"]) for row in rows} == {
        (table, column)
        for table in ("agent_edit_runs", "avatar_profiles", "avatar_render_jobs")
        for column in ("created_at", "updated_at")
    }
    assert all(row["column_default"] for row in rows)
