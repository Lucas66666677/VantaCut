"""Each release-preflight rule is exercised against a real regression, not a mock.

The fixture copies the actual release files into a temporary tree, so every case starts
from wiring that is known to pass and then breaks exactly one thing. A rule that was
silently disabled — a regex that stopped matching, a file that moved — shows up here as
a test that no longer fails when it should.

    python -m pytest tests/preflight
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_PATH = ROOT / "scripts" / "release_preflight.py"

RELEASE_FILES = (
    "docker-compose.yml",
    "docker-compose.production.yml",
    "docker-compose.spatial.yml",
    ".env.example",
    ".env.production.example",
    "infra/nginx/default.conf",
    "backend/app/main.py",
    "backend/Dockerfile",
    "backend/Dockerfile.api",
    "backend/Dockerfile.worker",
    "frontend/Dockerfile",
    "frontend/Dockerfile.production",
    ".github/workflows/render-quality.yml",
)


def _load_preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_preflight", PREFLIGHT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise RuntimeError(f"cannot load {PREFLIGHT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load_preflight()


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A copy of the real release wiring, with the Docker step disabled."""
    root = tmp_path / "release"
    for relative in RELEASE_FILES:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    # Compose syntax needs the real binary; it has dedicated tests further down.
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
    yield root


def patch(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"{relative}: anchor not found: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def failures(root: Path) -> list[str]:
    return preflight.run_preflight(root).failures


def test_release_wiring_passes(release: Path) -> None:
    assert failures(release) == []


def test_this_repository_passes_every_check_that_needs_no_tooling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate's purpose: the committed release wiring stays consistent."""
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
    assert preflight.run_preflight(ROOT).failures == []


def test_fail_closed_variable_must_be_documented(release: Path) -> None:
    """`${VAR:?message}` requires a value just as much as a bare `${VAR}` does."""
    patch(release, ".env.example", "THREE_DGS_REPO_PATH=", "UNRELATED_PATH=")
    assert any("THREE_DGS_REPO_PATH" in failure for failure in failures(release))


def test_undocumented_production_variable_is_rejected(release: Path) -> None:
    patch(release, ".env.production.example", "NEXT_PUBLIC_API_URL=", "RENAMED_API_URL=")
    assert any("NEXT_PUBLIC_API_URL" in failure for failure in failures(release))


def test_missing_production_setting_is_rejected(release: Path) -> None:
    patch(release, ".env.production.example", "S3_SECRET_KEY=REPLACE_ME", "S3_SECRET=REPLACE_ME")
    assert any(
        "S3_SECRET_KEY" in failure and "development default" in failure
        for failure in failures(release)
    )


def test_mock_ai_left_enabled_for_production_is_rejected(release: Path) -> None:
    """Shipping the mock provider as a release would sell an unusable product."""
    patch(release, ".env.production.example", "MOCK_AI=false", "MOCK_AI=true")
    assert any("MOCK_AI" in failure and "requires 'false'" in failure for failure in failures(release))


def test_non_production_environment_value_is_rejected(release: Path) -> None:
    patch(release, ".env.production.example", "ENVIRONMENT=production", "ENVIRONMENT=staging")
    assert any("ENVIRONMENT" in failure for failure in failures(release))


def test_development_credential_in_the_production_example_is_rejected(release: Path) -> None:
    patch(release, ".env.production.example", "S3_ACCESS_KEY=REPLACE_ME", "S3_ACCESS_KEY=minioadmin")
    assert any("development-stack value" in failure for failure in failures(release))


def test_real_looking_secret_in_the_production_example_is_rejected(release: Path) -> None:
    patch(
        release,
        ".env.production.example",
        "GEMINI_API_KEY=REPLACE_ME",
        "GEMINI_API_KEY=AIzaSyD9fK2mQ7xR4tW1nB8vL3cH6jP0sYzA5eU",
    )
    assert any("does not read as a placeholder" in failure for failure in failures(release))


def test_env_file_without_a_committed_example_is_rejected(release: Path) -> None:
    patch(release, "docker-compose.production.yml", "env_file: .env.production",
          "env_file: .env.secrets")
    assert any(".env.secrets.example is not committed" in failure for failure in failures(release))


def test_healthcheck_binary_absent_from_the_image_is_rejected(release: Path) -> None:
    """A probe that shells out to a binary the image lacks never returns healthy."""
    patch(release, "docker-compose.production.yml", '"curl", "--fail"', '"wget", "--spider"')
    assert any("never installs" in failure for failure in failures(release))


def test_healthcheck_against_an_unexposed_port_is_rejected(release: Path) -> None:
    patch(release, "docker-compose.production.yml", "http://localhost:8000/health",
          "http://localhost:9999/health")
    assert any("never EXPOSEs" in failure for failure in failures(release))


def test_healthcheck_against_an_undeclared_route_is_rejected(release: Path) -> None:
    patch(release, "docker-compose.production.yml", "http://localhost:8000/health",
          "http://localhost:8000/healthz")
    assert any("does not declare as a route" in failure for failure in failures(release))


def test_removing_the_readiness_route_is_rejected(release: Path) -> None:
    patch(release, "backend/app/main.py", '@app.get("/ready")', '@app.get("/warm")')
    assert any("/ready" in failure for failure in failures(release))


def test_service_healthy_dependency_without_a_healthcheck_is_rejected(release: Path) -> None:
    patch(release, "docker-compose.yml", '      test: ["CMD", "redis-cli", "ping"]',
          '      disabled: ["CMD", "redis-cli", "ping"]')
    patch(release, "docker-compose.yml", "    healthcheck:\n      disabled:", "    x-was:\n      disabled:")
    assert any("declares no healthcheck" in failure for failure in failures(release))


def test_service_healthy_dependency_on_an_undefined_service_is_rejected(release: Path) -> None:
    patch(release, "docker-compose.yml", "      postgres:\n        condition: service_healthy",
          "      postgress:\n        condition: service_healthy")
    assert any("undefined service postgress" in failure for failure in failures(release))


def test_reverse_proxy_to_an_unknown_service_is_rejected(release: Path) -> None:
    patch(release, "infra/nginx/default.conf", "proxy_pass http://frontend:3000",
          "proxy_pass http://web:3000")
    assert any("not a service" in failure for failure in failures(release))


def test_reverse_proxy_to_a_closed_port_is_rejected(release: Path) -> None:
    patch(release, "infra/nginx/default.conf", "proxy_pass http://frontend:3000",
          "proxy_pass http://frontend:8080")
    assert any("that service opens" in failure for failure in failures(release))


def test_workflow_waiting_on_an_undeclared_route_is_rejected(release: Path) -> None:
    patch(release, ".github/workflows/render-quality.yml", "http://127.0.0.1:8000/health",
          "http://127.0.0.1:8000/healthy")
    assert any("would time out on every run" in failure for failure in failures(release))


def test_router_paths_in_the_workflow_are_not_route_checked(release: Path) -> None:
    """The render gate exercises /api paths for real; they carry parameters, not literals."""
    workflow = (release / ".github/workflows/render-quality.yml").read_text(encoding="utf-8")
    assert "/api/v1/timelines/$timeline_id/render" in workflow
    assert failures(release) == []


def test_published_port_mapping_survives_a_defaulted_variable(release: Path) -> None:
    """"${BACKEND_PORT:-8000}:8000" has to resolve to host port 8000."""
    compose = preflight.load_compose(release, "docker-compose.yml")
    assert preflight.published_ports(compose)[8000] == "backend"
    assert preflight.published_ports(compose)[8189] == "mediamtx"


def test_compose_syntax_is_skipped_without_docker(release: Path) -> None:
    report = preflight.run_preflight(release)
    assert any("docker is unavailable" in skip for skip in report.skips)
    assert report.failures == []


def test_compose_syntax_failure_is_reported(release: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="services.api: unknown key"
        ),
    )
    assert any("docker compose config` failed" in failure for failure in failures(release))


def test_every_compose_file_is_validated(release: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The spatial file extends the base file, so it is validated as an overlay."""
    invocations: list[list[str]] = []

    def record(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        invocations.append(command)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(preflight.subprocess, "run", record)
    report = preflight.run_preflight(release)

    assert report.failures == []
    files = [[arg for arg in command if arg.endswith(".yml")] for command in invocations]
    assert ["docker-compose.yml"] in files
    assert ["docker-compose.yml", "docker-compose.spatial.yml"] in files
    assert ["docker-compose.production.yml"] in files


def test_syntax_check_stands_in_for_the_uncommitted_production_env_file(
    release: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose cannot parse the production file without .env.production."""
    seen: dict[str, bool] = {}

    def observe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "docker-compose.production.yml" in command:
            seen["present"] = (release / ".env.production").is_file()
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(preflight.subprocess, "run", observe)
    preflight.run_preflight(release)

    assert seen["present"] is True
    # And it is cleaned up, so an interrupted release never leaves a secret-shaped file.
    assert not (release / ".env.production").exists()


def test_an_existing_production_env_file_is_never_touched(
    release: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_env = release / ".env.production"
    operator_env.write_text("DATABASE_URL=postgresql://real:secret@host/db\n", encoding="utf-8")
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        ),
    )
    preflight.run_preflight(release)
    assert operator_env.read_text(encoding="utf-8").startswith("DATABASE_URL=postgresql://real")


def test_synthetic_syntax_values_are_never_credentials(release: Path) -> None:
    """The stand-ins exist so compose can parse; they must not look like real values."""
    environment = preflight._syntax_environment(
        release, ["docker-compose.spatial.yml"], ".env.example"
    )
    assert environment["THREE_DGS_REPO_PATH"] == preflight.SYNTHETIC_CONFIG_VALUE
    assert "preflight" in preflight.SYNTHETIC_CONFIG_VALUE


def test_unavailable_docker_binary_is_a_skip_not_a_failure(
    release: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/docker")

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("cannot execute docker")

    monkeypatch.setattr(preflight.subprocess, "run", explode)
    report = preflight.run_preflight(release)
    assert report.failures == []
    assert len(report.skips) == 3


def test_main_exits_non_zero_and_explains_the_failure(
    release: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    patch(release, "infra/nginx/default.conf", "proxy_pass http://frontend:3000",
          "proxy_pass http://web:3000")
    assert preflight.main(["--root", str(release)]) == 1
    assert "Release preflight failed" in capsys.readouterr().out


def test_main_exits_zero_on_a_consistent_release(
    release: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert preflight.main(["--root", str(release)]) == 0
    assert "Release preflight passed" in capsys.readouterr().out
