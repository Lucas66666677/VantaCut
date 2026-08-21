"""Regression for the voice_profiles server-managed timestamp contract."""
from sqlalchemy import text


def test_voice_profiles_timestamps_have_database_defaults(db_session):
    rows = db_session.execute(text("""
        SELECT column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'voice_profiles'
          AND column_name IN ('created_at', 'updated_at')
    """)).mappings().all()
    assert {row["column_name"] for row in rows} == {"created_at", "updated_at"}
    assert all(row["column_default"] for row in rows)
