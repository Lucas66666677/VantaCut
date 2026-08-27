"""Storage readiness must stay observable and must never leak configuration."""

import importlib.util
from pathlib import Path

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


def _config_default_with_env_unset(monkeypatch, variable: str, attribute: str) -> str:
    """The value `attribute` falls back to when `variable` is absent from the environment.

    app/core/config.py calls os.getenv in the Settings class body, so every
    fallback is frozen at import time and cannot be read back off the live
    `settings` object -- whoever runs this may perfectly well have
    S3_ENDPOINT_URL set. Executing the module body again under a throwaway
    module name (the same load-by-file-path trick tests/conftest.py uses for
    auth.py) re-evaluates the defaults without touching
    sys.modules["app.core.config"], and so without disturbing the `settings`
    instance app.services.storage_readiness holds a reference to.
    """
    monkeypatch.delenv(variable, raising=False)
    spec = importlib.util.spec_from_file_location(
        "_vantacut_test_config_defaults", Path(config.__file__)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module.Settings, attribute)


def test_development_endpoint_constant_matches_the_real_config_default(monkeypatch) -> None:
    """DEVELOPMENT_S3_ENDPOINT copies a literal that lives in a different file.

    `configured` is the entire diagnostic: it is False precisely when nobody
    ever set S3_ENDPOINT_URL, which this module detects by comparing the live
    value against config.py's fallback. Nothing links the two literals. Change
    config.py's default -- to the compose service name, say -- and every test
    above still passes, because they all set the endpoint themselves, while
    /ready/storage quietly starts answering `configured: true` for a deployment
    that has no object storage at all. That is worse than the silence this
    endpoint was added to break, because it is a positive all-clear.
    """
    assert (
        _config_default_with_env_unset(monkeypatch, "S3_ENDPOINT_URL", "s3_endpoint_url")
        == DEVELOPMENT_S3_ENDPOINT
    )
