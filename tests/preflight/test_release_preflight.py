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

# Anchors that span two YAML lines are built from this rather than an escape, so the
# indentation they have to match stays visible in the source.
NEWLINE = chr(10)

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
    "render.yaml",
    "backend/alembic.ini",
    "backend/start.sh",
    "backend/app/api/v1/media.py",
)

# The migration chain is read off disk, so the fixture needs the real revision files.
RELEASE_TREES = ("backend/migrations",)


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
    for relative in RELEASE_TREES:
        shutil.copytree(
            ROOT / relative,
            root / relative,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    # Compose syntax needs the real binary; it has dedicated tests further down.
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
    yield root


def patch(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"{relative}: anchor not found: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def add_frontend_domain(root: Path, domain: str) -> None:
    """Declare a custom domain on the Render frontend service, as an owner would."""
    patch(
        root,
        "render.yaml",
        f"    name: vantacut-frontend{NEWLINE}",
        f"    name: vantacut-frontend{NEWLINE}    domains:{NEWLINE}      - {domain}{NEWLINE}",
    )


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


def test_render_health_gate_pointed_at_readiness_is_rejected(release: Path) -> None:
    """/ready 503s when PostgreSQL or Redis blips; Render would restart a healthy API."""
    patch(release, "render.yaml", "healthCheckPath: /health", "healthCheckPath: /ready")
    assert any(
        "/ready" in failure and "liveness route /health" in failure
        for failure in failures(release)
    )


def test_render_health_gate_pointed_at_a_readiness_subpath_is_rejected(release: Path) -> None:
    patch(release, "render.yaml", "healthCheckPath: /health", "healthCheckPath: /ready/storage")
    assert any("readiness route" in failure for failure in failures(release))


def test_render_health_gate_on_an_undeclared_route_is_rejected(release: Path) -> None:
    """Renaming the liveness route without updating the blueprint fails every deploy."""
    patch(release, "backend/app/main.py", '@app.get("/health")', '@app.get("/healthz")')
    assert any(
        "render.yaml" in failure and "does not declare as a route" in failure
        for failure in failures(release)
    )


def test_render_backend_service_without_a_health_gate_is_rejected(release: Path) -> None:
    patch(release, "render.yaml", "healthCheckPath: /health", "# healthCheckPath: removed")
    assert any("declares no healthCheckPath" in failure for failure in failures(release))


def test_renaming_the_frontend_service_breaks_cors_and_is_rejected(release: Path) -> None:
    """A rename moves the app to a new origin; the API keeps allowing only the old one."""
    patch(release, "render.yaml", "name: vantacut-frontend", "name: vantacut-web")
    assert any(
        "CORS_ALLOWED_ORIGINS" in failure and "https://vantacut-web.onrender.com" in failure
        for failure in failures(release)
    )


def test_renaming_the_backend_service_strands_the_inlined_api_origin(release: Path) -> None:
    """The bundle would ship pointing at a host the blueprint no longer deploys."""
    patch(release, "render.yaml", "name: vantacut-backend", "name: vantacut-api")
    assert any(
        "NEXT_PUBLIC_API_URL" in failure and "does not deploy" in failure
        for failure in failures(release)
    )


def test_a_public_https_origin_for_an_undeployed_host_is_rejected(release: Path) -> None:
    """check-public-api-origin.mjs passes this: it is a bare, public, https origin.

    That guard rejects loopback, private and malformed origins. It cannot know which
    host actually runs the API, so only the blueprint can catch a stale but well-formed
    one -- which is exactly what a service rename leaves behind.
    """
    patch(
        release,
        "render.yaml",
        f"value: https://vantacut-backend.onrender.com{NEWLINE}",
        f"value: https://vantacut-backend-old.onrender.com{NEWLINE}",
    )
    assert any("vantacut-backend-old.onrender.com" in failure for failure in failures(release))


def test_a_custom_domain_missing_from_cors_is_rejected(release: Path) -> None:
    """Render keeps serving the app on every declared domain, so all of them need CORS."""
    add_frontend_domain(release, "app.vantacut.com")
    assert any(
        "https://app.vantacut.com" in failure and "matches origins exactly" in failure
        for failure in failures(release)
    )


def test_a_custom_domain_listed_in_cors_passes(release: Path) -> None:
    add_frontend_domain(release, "app.vantacut.com")
    patch(
        release,
        "render.yaml",
        "value: https://vantacut-frontend.onrender.com",
        "value: https://vantacut-frontend.onrender.com,https://app.vantacut.com",
    )
    assert failures(release) == []


def test_a_dashboard_managed_origin_is_a_skip_not_a_pass(release: Path) -> None:
    """`sync: false` hides the value, so the blueprint cannot prove the pairing."""
    patch(
        release,
        "render.yaml",
        f"- key: CORS_ALLOWED_ORIGINS{NEWLINE}        value: https://vantacut-frontend.onrender.com",
        f"- key: CORS_ALLOWED_ORIGINS{NEWLINE}        sync: false",
    )
    report = preflight.run_preflight(release)
    assert report.failures == []
    assert any("CORS_ALLOWED_ORIGINS is dashboard-managed" in skip for skip in report.skips)


def test_dropping_cors_from_the_blueprint_is_rejected(release: Path) -> None:
    """Unset, main.py falls back to http://localhost:3000 and the deployed app is blocked."""
    patch(release, "render.yaml", "- key: CORS_ALLOWED_ORIGINS", "- key: CORS_UNUSED")
    assert any("sets no CORS_ALLOWED_ORIGINS" in failure for failure in failures(release))


def test_an_oauth_redirect_base_pointing_at_the_frontend_is_rejected(release: Path) -> None:
    """The callback route lives on the API, so the base URL has to be the API's origin."""
    patch(
        release,
        "render.yaml",
        f"- key: SOCIAL_OAUTH_REDIRECT_BASE_URL{NEWLINE}"
        "        value: https://vantacut-backend.onrender.com",
        f"- key: SOCIAL_OAUTH_REDIRECT_BASE_URL{NEWLINE}"
        "        value: https://vantacut-frontend.onrender.com",
    )
    assert any(
        "SOCIAL_OAUTH_REDIRECT_BASE_URL" in failure and "itself" in failure
        for failure in failures(release)
    )


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


# --- database migrations -------------------------------------------------------------
# `alembic upgrade head` runs on every Render boot (backend/start.sh) and by hand at step
# 4 of docs/launch-readiness.md. These cases are the ways that command stops meaning
# "the production schema is now the one the code expects".

HEAD_MIGRATION = "backend/migrations/versions/0040_fix_review_social_timestamp_defaults.py"


def write_migration(root: Path, name: str, revision: str, down: str) -> None:
    (root / "backend/migrations/versions" / name).write_text(
        f'revision = "{revision}"\ndown_revision = {down}\n'
        "def upgrade() -> None: ...\ndef downgrade() -> None: ...\n",
        encoding="utf-8",
    )


def test_a_second_head_is_rejected(release: Path) -> None:
    """Two heads make `alembic upgrade head` abort, so the container never starts."""
    patch(release, HEAD_MIGRATION,
          'down_revision = "0039_fix_ai_assistance_timestamp_defaults"',
          'down_revision = "0038_fix_voice_profiles_timestamp_defaults"')
    assert any("exactly one head, found 2" in failure for failure in failures(release))


def test_down_revision_naming_a_deleted_migration_is_rejected(release: Path) -> None:
    patch(release, HEAD_MIGRATION,
          'down_revision = "0039_fix_ai_assistance_timestamp_defaults"',
          'down_revision = "0039_renamed_while_rebasing"')
    assert any(
        "does not exist" in failure and "0039_renamed_while_rebasing" in failure
        for failure in failures(release)
    )


def test_duplicate_revision_id_is_rejected(release: Path) -> None:
    """Two files claiming one id make the chain ambiguous rather than merely long."""
    write_migration(release, "0041_copy_paste.py",
                    "0040_fix_review_social_timestamp_defaults",
                    '"0039_fix_ai_assistance_timestamp_defaults"')
    assert any("duplicate revision id" in failure for failure in failures(release))


def test_migrations_head_cannot_reach_are_rejected(release: Path) -> None:
    """A detached pair is never applied, so production runs a schema the code outgrew."""
    write_migration(release, "0041_detached_a.py", "0041_detached_a", '"0041_detached_b"')
    write_migration(release, "0041_detached_b.py", "0041_detached_b", '"0041_detached_a"')
    assert any("cannot be reached from head" in failure for failure in failures(release))


def test_versions_directory_without_migrations_is_rejected(release: Path) -> None:
    """The quiet failure: `alembic upgrade head` exits 0 having applied nothing."""
    for migration in (release / "backend/migrations/versions").glob("*.py"):
        migration.unlink()
    assert any("applied nothing" in failure for failure in failures(release))


def test_script_location_pointing_away_from_the_migrations_is_rejected(release: Path) -> None:
    patch(release, "backend/alembic.ini", "script_location = migrations",
          "script_location = migrations_v2")
    assert any("no env.py" in failure for failure in failures(release))


def test_entrypoint_that_never_applies_migrations_is_rejected(release: Path) -> None:
    patch(release, "backend/start.sh", "alembic upgrade head", "# alembic upgrade head")
    assert any("never runs `alembic upgrade head`" in failure for failure in failures(release))


def test_entrypoint_that_continues_after_a_failed_migration_is_rejected(release: Path) -> None:
    """Without `set -e`, gunicorn starts anyway and serves the un-migrated schema."""
    patch(release, "backend/start.sh", "set -e\n", "")
    assert any("would not stop the API from starting" in failure for failure in failures(release))

MEDIA_MODULE = "backend/app/api/v1/media.py"


def test_upload_endpoints_fail_closed_passes_on_the_committed_source(release: Path) -> None:
    """The guarded upload routes are part of the release-wiring baseline."""
    assert failures(release) == []


def test_an_upload_route_that_drops_the_storage_guard_is_rejected(release: Path) -> None:
    """Removing the guard from create_media_upload_url must fail the release."""
    patch(
        release,
        MEDIA_MODULE,
        "    _require_storage_configured()\n    asset = _create_uploading_asset(payload, current_user, db)\n    return UploadURLResponse(",
        "    asset = _create_uploading_asset(payload, current_user, db)\n    return UploadURLResponse(",
    )
    assert any(
        "create_media_upload_url" in failure and "_require_storage_configured" in failure
        for failure in failures(release)
    )


def test_a_guard_that_stops_consulting_storage_is_rejected(release: Path) -> None:
    """A guard that no longer calls storage_is_configured is a no-op gate."""
    patch(
        release,
        MEDIA_MODULE,
        "    if not storage_is_configured():",
        "    if False:",
    )
    assert any(
        "_require_storage_configured() is missing or no longer consults" in failure
        for failure in failures(release)
    )


def test_the_check_fails_when_the_upload_path_disappears_entirely(release: Path) -> None:
    """A media module with no `@router` endpoints must not pass vacuously."""
    (release / MEDIA_MODULE).write_text("x = 1\n", encoding="utf-8")
    assert any(
        "no `@router` endpoints found" in failure for failure in failures(release)
    )
