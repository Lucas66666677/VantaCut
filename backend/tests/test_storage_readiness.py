"""Storage readiness must stay observable and must never leak configuration."""

import pytest

from app.core import config
from app.services.storage_readiness import DEVELOPMENT_S3_ENDPOINT, storage_readiness


@pytest.fixture
def s3_endpoint(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setattr(config.settings, "s3_endpoint_url", value, raising=False)

    return _set


def test_development_default_endpoint_is_reported_as_unconfigured(s3_endpoint) -> None:
    """A deployment that never set S3_ENDPOINT_URL must not look healthy.

    This is the whole point: /health and /ready both return 200 in that state
    while every upload fails.
    """
    s3_endpoint(DEVELOPMENT_S3_ENDPOINT)
    body = storage_readiness()

    assert body["configured"] is False
    assert body["uploads_expected_to_work"] is False


def test_unconfigured_endpoint_is_not_probed_over_the_network(s3_endpoint, monkeypatch) -> None:
    s3_endpoint(DEVELOPMENT_S3_ENDPOINT)
    called = False

    def _fail() -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr("app.services.storage_readiness._probe_bucket", _fail)
    storage_readiness()

    assert called is False


def test_result_is_booleans_only(s3_endpoint, monkeypatch) -> None:
    """The endpoint is unauthenticated, so it may only ever return booleans."""
    s3_endpoint("https://s3.example.invalid")
    monkeypatch.setattr("app.services.storage_readiness._probe_bucket", lambda: True)
    body = storage_readiness()

    assert set(body) == {"configured", "bucket_reachable", "uploads_expected_to_work"}
    assert all(isinstance(value, bool) for value in body.values())
    assert body["uploads_expected_to_work"] is True


def test_unreachable_bucket_blocks_uploads_expected_to_work(s3_endpoint, monkeypatch) -> None:
    s3_endpoint("https://s3.example.invalid")
    monkeypatch.setattr("app.services.storage_readiness._probe_bucket", lambda: False)
    body = storage_readiness()

    assert body["configured"] is True
    assert body["bucket_reachable"] is False
    assert body["uploads_expected_to_work"] is False
