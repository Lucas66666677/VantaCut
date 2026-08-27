"""CI release preflight: verify the release wiring without holding a single secret.

The render quality gate proves the product renders. It says nothing about whether the
*production* stack could start: it runs `docker-compose.yml` with MOCK_AI=true and dev
credentials, and never looks at `docker-compose.production.yml`, `.env.production.example`
or `infra/nginx/default.conf` at all. A production compose file that waits on a health
state no service can reach, an nginx upstream pointing at a port nothing opens, or a
production example still carrying `minioadmin` would all reach a release untouched.

This script closes that gap from the repository alone. It reads the compose files, the
env examples, the Dockerfiles, the nginx config, the render workflow, the Render
blueprint and the FastAPI application, and it never reads a real `.env`: a preflight
that needs a secret cannot run before the secret exists.

    python scripts/release_preflight.py

Compose syntax validation shells out to `docker compose config`. Where Docker is
unavailable the check reports as skipped rather than passing, so the script stays
usable outside CI without ever pretending it verified something it did not.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

DEV_COMPOSE = "docker-compose.yml"
PRODUCTION_COMPOSE = "docker-compose.production.yml"
SPATIAL_COMPOSE = "docker-compose.spatial.yml"
DEV_ENV_EXAMPLE = ".env.example"
PRODUCTION_ENV_EXAMPLE = ".env.production.example"
NGINX_CONF = "infra/nginx/default.conf"
API_MODULE = "backend/app/main.py"
RENDER_WORKFLOW = ".github/workflows/render-quality.yml"
RENDER_BLUEPRINT = "render.yaml"
ALEMBIC_INI = "backend/alembic.ini"
BACKEND_ENTRYPOINT = "backend/start.sh"

# Compose files paired with the example that documents the values they interpolate.
COMPOSE_SOURCES = (
    (DEV_COMPOSE, DEV_ENV_EXAMPLE),
    (SPATIAL_COMPOSE, DEV_ENV_EXAMPLE),
    (PRODUCTION_COMPOSE, PRODUCTION_ENV_EXAMPLE),
)

INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*)([^}]*)\}")
# "$$" hands the literal "$" to the container's own shell; those are not values this
# repository has to supply.
ESCAPED_INTERPOLATION = re.compile(r"\$\$\{?[A-Z][A-Z0-9_]*\}?")
EXPOSE_DIRECTIVE = re.compile(r"^EXPOSE\s+(\d+)", re.M)
PROBE_URL = re.compile(r"https?://([A-Za-z0-9_.-]+):(\d+)(/[^'\"\s)]*)")
FASTAPI_ROUTE = re.compile(r"@app\.(?:get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']")
NGINX_UPSTREAM = re.compile(r"proxy_pass\s+https?://([A-Za-z0-9_.-]+):(\d+)")
LOOPBACK_URL = re.compile(r"http://127\.0\.0\.1:(\d+)(/[^\s'\"]*)")

# Liveness and readiness are separate deployment contracts: the container healthcheck
# restarts on liveness, a load balancer drains on readiness. Both must keep existing.
REQUIRED_API_ROUTES = ("/health", "/ready")

# The Render health gate restarts the container; it is not a load-balancer drain. So it
# has to be the liveness route, and never a readiness one: /ready opens PostgreSQL and
# Redis and answers 503 when either is unreachable, and both are externally managed
# instances, so a dependency blip would restart an API container that is itself fine --
# on the free tier, into a cold-start loop. `/ready/storage` is readiness too, hence the
# prefix match rather than an equality test.
LIVENESS_ROUTE = "/health"
READINESS_ROUTES = ("/ready",)
# Render services built from this path are the API; the frontend service has no route
# contract with backend/app/main.py.
BACKEND_BUILD_PREFIX = "backend/"

# Settings the production stack must state outright. Each has a development default in
# backend/app/core/config.py or backend/app/db/session.py that would otherwise apply
# silently to a production deployment.
PRODUCTION_REQUIRED_KEYS = (
    "ENVIRONMENT",
    "MOCK_AI",
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_BUCKET",
    "CORS_ALLOWED_ORIGINS",
    "NEXT_PUBLIC_API_URL",
)
PRODUCTION_EXACT_VALUES = {"ENVIRONMENT": "production", "MOCK_AI": "false"}

# Values that belong to the local development stack and must never appear in the
# production example, where they read as a deployable configuration.
DEVELOPMENT_ONLY_VALUES = ("minioadmin", "video_editor_dev", "localhost", "127.0.0.1")

# The production example is committed, so every value in it must read as a template.
PLACEHOLDER_MARKERS = ("replace_me", "replace-me", "example.com", "your-", "changeme", "user:password")
# Keys whose example value is a real, non-secret setting rather than a placeholder.
NON_SECRET_KEYS = {
    "ENVIRONMENT",
    "MOCK_AI",
    "AI_VISION_PROVIDER",
    "AI_ASR_PROVIDER",
    "AI_TEXT_PROVIDER",
    "AI_DIRECTOR_PROVIDER",
    "AI_EMBEDDING_PROVIDER",
    "OPENAI_DIRECTOR_MODEL",
    "OPENCLIP_MODEL",
    "OPENCLIP_PRETRAINED",
    "KINETIC_SUBTITLE_WEBM",
    "VIDEO_INPAINTING_PROVIDER",
    "CELERY_INPAINTING_QUEUE",
    "SAM2_CONFIG_PATH",
    "MATTING_FRAME_STRIDE",
    "S3_BUCKET",
    "IMAGE_TAG",
    "TLS_CERTS_PATH",
}

# Stand-in for values the examples deliberately leave blank, used only so that
# `docker compose config` can parse a file. It is never a credential.
SYNTHETIC_CONFIG_VALUE = "release-preflight-syntax-check-only"

# Commands a healthcheck may use without the image installing anything.
SHELL_BUILTIN_PROBES = {"sh", "/bin/sh", "bash", "/bin/bash", "test", "true"}

# `alembic upgrade head` is the first thing backend/start.sh runs on every Render boot,
# and step 4 of docs/launch-readiness.md is the same command by hand. Both read the
# revision graph off disk, so a graph that cannot resolve is a repository fact this gate
# can prove without a database, a DATABASE_URL, or any other secret.
#
# These are matched against one line at a time, so no pattern has to reason about
# where a line ends.
SCRIPT_LOCATION = re.compile(r"script_location\s*=\s*(.+?)\s*$")
# `revision: str = "x"` is the modern template; `revision = "x"` is the older one.
REVISION_ID = re.compile(r"revision\s*(?::[^=]+)?=\s*[\"']([^\"']+)[\"']")
DOWN_REVISION = re.compile(r"down_revision\s*(?::[^=]+)?=\s*(.+?)\s*$")
QUOTED = re.compile(r"[\"']([^\"']+)[\"']")
UPGRADE_TO_HEAD = re.compile(r"alembic\s+upgrade\s+head\s*$")
ABORT_ON_ERROR = re.compile(r"set\s+-[a-z]*e[a-z]*\s*$")


class Report:
    """Collected outcomes. Failures block the release; skips are reported, not hidden."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.skips: list[str] = []
        self.passed: list[str] = []

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def skip(self, message: str) -> None:
        self.skips.append(message)

    def ok(self, message: str) -> None:
        self.passed.append(message)


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def load_compose(root: Path, relative: str) -> dict[str, Any]:
    document = yaml.safe_load(_read(root, relative))
    if not isinstance(document, dict):
        raise ValueError(f"{relative} does not parse as a mapping")
    return document


def interpolated_names(root: Path, relative: str) -> tuple[set[str], set[str]]:
    """Compose interpolations, split into required and defaulted.

    `${X}` and `${X:?message}` both require a value from the environment; only the
    `${X-default}` and `${X:-default}` forms supply one.
    """
    text = ESCAPED_INTERPOLATION.sub("", _read(root, relative))
    required: set[str] = set()
    defaulted: set[str] = set()
    for name, suffix in INTERPOLATION.findall(text):
        if suffix.lstrip(":").startswith("-"):
            defaulted.add(name)
        else:
            required.add(name)
    return required, defaulted - required


def env_entries(root: Path, relative: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in _read(root, relative).splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        entries[key.strip()] = value.strip()
    return entries


def check_documented_variables(root: Path, report: Report) -> None:
    """A value the stack cannot start without must be documented somewhere."""
    failed = False
    total = 0
    for compose_file, example in COMPOSE_SOURCES:
        required, _ = interpolated_names(root, compose_file)
        documented = env_entries(root, example)
        total += len(required)
        for name in sorted(required - documented.keys()):
            failed = True
            report.fail(
                f"{compose_file} requires {name} with no default, but {example} does not "
                f"document it; an operator has no way to know the stack needs it."
            )
    if not failed:
        report.ok(f"all {total} required compose variables are documented in an env example")


def check_production_configuration(root: Path, report: Report) -> None:
    """The production example must describe a production deployment, not a laptop."""
    entries = env_entries(root, PRODUCTION_ENV_EXAMPLE)

    missing = [name for name in PRODUCTION_REQUIRED_KEYS if name not in entries]
    for name in missing:
        report.fail(
            f"{PRODUCTION_ENV_EXAMPLE} does not set {name}; the deployment would silently "
            f"fall back to the development default compiled into the backend."
        )

    wrong = {
        name: entries[name]
        for name, expected in PRODUCTION_EXACT_VALUES.items()
        if name in entries and entries[name].strip().lower() != expected
    }
    for name, value in wrong.items():
        report.fail(
            f"{PRODUCTION_ENV_EXAMPLE}: {name} is {value!r}, but a production release "
            f"requires {PRODUCTION_EXACT_VALUES[name]!r}."
        )

    leaked = sorted(
        f"{name}={value}"
        for name, value in entries.items()
        if any(marker in value.lower() for marker in DEVELOPMENT_ONLY_VALUES)
    )
    for entry in leaked:
        report.fail(
            f"{PRODUCTION_ENV_EXAMPLE}: {entry} carries a development-stack value; the "
            f"production example must describe managed hosts and credentials only."
        )

    if not missing and not wrong and not leaked:
        report.ok(f"{PRODUCTION_ENV_EXAMPLE} describes a production deployment")


def check_example_holds_no_secrets(root: Path, report: Report) -> None:
    offenders: list[str] = []
    for name, value in env_entries(root, PRODUCTION_ENV_EXAMPLE).items():
        if not value or name in NON_SECRET_KEYS:
            continue
        if any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
            continue
        offenders.append(name)
    for name in offenders:
        report.fail(
            f"{PRODUCTION_ENV_EXAMPLE}: {name} holds a value that does not read as a "
            f"placeholder. A committed example must never carry a usable credential."
        )
    if not offenders:
        report.ok(f"{PRODUCTION_ENV_EXAMPLE} carries placeholders only")


def check_env_files_have_examples(root: Path, report: Report) -> None:
    """Every secret file a service loads needs a committed template beside it."""
    compose = load_compose(root, PRODUCTION_COMPOSE)
    missing: list[str] = []
    referenced = 0
    for name, service in compose.get("services", {}).items():
        env_file = service.get("env_file")
        targets = [env_file] if isinstance(env_file, str) else list(env_file or [])
        for target in targets:
            referenced += 1
            if not (root / f"{target}.example").is_file():
                missing.append(f"{name} loads {target}, but {target}.example is not committed")
    for detail in missing:
        report.fail(f"{PRODUCTION_COMPOSE}: {detail}.")
    if not missing:
        report.ok(f"all {referenced} production env_file references have a committed example")


def dockerfile_for(service: dict[str, Any]) -> str | None:
    build = service.get("build")
    if isinstance(build, dict):
        context = str(build.get("context", ".")).lstrip("./")
        dockerfile = str(build.get("dockerfile", "Dockerfile"))
        return f"{context}/{dockerfile}" if context else dockerfile
    if isinstance(build, str):
        return f"{build.lstrip('./')}/Dockerfile"
    return None


def exposed_ports(root: Path, dockerfile: str | None) -> set[int]:
    if dockerfile is None or not (root / dockerfile).is_file():
        return set()
    return {int(port) for port in EXPOSE_DIRECTIVE.findall(_read(root, dockerfile))}


def healthcheck_command(service: dict[str, Any]) -> list[str]:
    test = (service.get("healthcheck") or {}).get("test")
    if isinstance(test, str):
        return ["CMD-SHELL", test]
    return [str(item) for item in test] if isinstance(test, list) else []


def declared_routes(root: Path) -> set[str]:
    return set(FASTAPI_ROUTE.findall(_read(root, API_MODULE)))


def check_health_probes(root: Path, report: Report) -> None:
    """A probe that cannot succeed keeps a service permanently unhealthy."""
    routes = declared_routes(root)
    compose = load_compose(root, PRODUCTION_COMPOSE)
    failed = False
    checked = 0
    for name, service in compose.get("services", {}).items():
        command = healthcheck_command(service)
        if not command:
            continue
        dockerfile = dockerfile_for(service)
        exposed = exposed_ports(root, dockerfile)
        binary = command[1].split()[0] if command[0] == "CMD-SHELL" else (command[1:2] or [""])[0]
        if binary and binary not in SHELL_BUILTIN_PROBES and dockerfile:
            if not re.search(rf"\b{re.escape(binary)}\b", _read(root, dockerfile)):
                failed = True
                report.fail(
                    f"{PRODUCTION_COMPOSE}: the {name} healthcheck runs {binary!r}, which "
                    f"{dockerfile} never installs; the service would never report healthy."
                )
        for _, port, path in PROBE_URL.findall(" ".join(command)):
            checked += 1
            if exposed and int(port) not in exposed:
                failed = True
                report.fail(
                    f"{PRODUCTION_COMPOSE}: the {name} healthcheck probes port {port}, which "
                    f"{dockerfile} never EXPOSEs (exposed: {sorted(exposed)})."
                )
            if path not in routes:
                failed = True
                report.fail(
                    f"{PRODUCTION_COMPOSE}: the {name} healthcheck probes {path}, which "
                    f"{API_MODULE} does not declare as a route."
                )
    for route in REQUIRED_API_ROUTES:
        if route not in routes:
            failed = True
            report.fail(
                f"{API_MODULE} no longer declares {route}; liveness and readiness are "
                f"separate deployment contracts and both must exist."
            )
    if not failed:
        report.ok(f"{checked} production health probes address an exposed port and a real route")


def check_health_dependencies(root: Path, report: Report) -> None:
    """Waiting on a service that can never be healthy deadlocks the whole stack."""
    failed = False
    waits = 0
    for compose_file in (DEV_COMPOSE, SPATIAL_COMPOSE, PRODUCTION_COMPOSE):
        compose = load_compose(root, compose_file)
        services: dict[str, Any] = compose.get("services", {})
        for name, service in services.items():
            depends_on = service.get("depends_on", {})
            if not isinstance(depends_on, dict):
                continue
            for target, condition in depends_on.items():
                if (
                    not isinstance(condition, dict)
                    or condition.get("condition") != "service_healthy"
                ):
                    continue
                waits += 1
                target_service = services.get(target)
                if target_service is None:
                    # The spatial file is an overlay; its dependencies live in the base file.
                    target_service = load_compose(root, DEV_COMPOSE).get("services", {}).get(target)
                if target_service is None:
                    failed = True
                    report.fail(f"{compose_file}: {name} waits on undefined service {target}.")
                elif not target_service.get("healthcheck"):
                    failed = True
                    report.fail(
                        f"{compose_file}: {name} waits for {target} to be healthy, but "
                        f"{target} declares no healthcheck; startup would hang."
                    )
    if not failed:
        report.ok(f"all {waits} service_healthy dependencies target a service with a healthcheck")


def check_reverse_proxy_contract(root: Path, report: Report) -> None:
    """nginx is the only public entry point; its upstreams must be real, open ports."""
    compose = load_compose(root, PRODUCTION_COMPOSE)
    services: dict[str, Any] = compose.get("services", {})
    upstreams = NGINX_UPSTREAM.findall(_read(root, NGINX_CONF))
    if not upstreams:
        report.fail(f"{NGINX_CONF}: no proxy_pass upstream found to verify.")
        return
    failed = False
    for host, port in upstreams:
        service = services.get(host)
        if service is None:
            failed = True
            report.fail(
                f"{NGINX_CONF}: proxies to {host}, which is not a service in {PRODUCTION_COMPOSE}."
            )
            continue
        published = {int(value) for value in service.get("expose", [])}
        published |= exposed_ports(root, dockerfile_for(service))
        if published and int(port) not in published:
            failed = True
            report.fail(
                f"{NGINX_CONF}: proxies to {host}:{port}, but that service opens "
                f"{sorted(published)}."
            )
    if not failed:
        report.ok(f"{NGINX_CONF} proxies to {len(upstreams)} upstream(s) that open the named port")


def _resolve_defaults(text: str) -> str:
    """Replace `${VAR:-default}` with its default so a mapping can be read literally."""
    return re.sub(r"\$\{[A-Z][A-Z0-9_]*(?::?-([^}]*))?\}", lambda m: m.group(1) or "", text)


def published_ports(compose: dict[str, Any]) -> dict[int, str]:
    """Host port -> service name, for every published mapping in a compose file."""
    mapping: dict[int, str] = {}
    for name, service in compose.get("services", {}).items():
        for entry in service.get("ports", []) or []:
            # "${BACKEND_PORT:-8000}:8000" and "8189:8189/udp" both reduce to a host port.
            parts = _resolve_defaults(str(entry)).split("/")[0].split(":")
            if len(parts) < 2 or not parts[-2].isdigit():
                continue
            mapping[int(parts[-2])] = name
    return mapping


def check_workflow_probe_paths(root: Path, report: Report) -> None:
    """The render gate waits on URLs; those paths have to be real routes."""
    routes = declared_routes(root)
    compose = load_compose(root, DEV_COMPOSE)
    ports = published_ports(compose)
    failed = False
    checked = 0
    for port, path in LOOPBACK_URL.findall(_read(root, RENDER_WORKFLOW)):
        # Ports the compose stack does not publish belong to workflow-level services.
        if int(port) not in ports or path in ("", "/"):
            continue
        # Router paths under /api carry path parameters and shell interpolation, and the
        # render gate exercises them for real. Only the app-level probes are checked here.
        if "$" in path or path.startswith("/api/"):
            continue
        checked += 1
        if ports[int(port)] == "backend" and path not in routes:
            failed = True
            report.fail(
                f"{RENDER_WORKFLOW}: waits on {path} at port {port}, which {API_MODULE} "
                f"does not declare as a route; the gate would time out on every run."
            )
    if not failed:
        report.ok(f"{checked} workflow wait URL(s) address a declared route")


def _is_readiness_route(path: str) -> bool:
    return any(path == route or path.startswith(f"{route}/") for route in READINESS_ROUTES)


def check_render_health_gate(root: Path, report: Report) -> None:
    """Render restarts the API on this path, so it must be liveness and it must exist."""
    blueprint = yaml.safe_load(_read(root, RENDER_BLUEPRINT))
    if not isinstance(blueprint, dict):
        report.fail(f"{RENDER_BLUEPRINT} does not parse as a mapping.")
        return
    routes = declared_routes(root)
    failed = False
    checked = 0
    for service in blueprint.get("services", []) or []:
        if not isinstance(service, dict):
            continue
        dockerfile = str(service.get("dockerfilePath", "")).lstrip("./")
        if not dockerfile.startswith(BACKEND_BUILD_PREFIX):
            continue
        name = service.get("name", "<unnamed>")
        path = service.get("healthCheckPath")
        if not path:
            failed = True
            report.fail(
                f"{RENDER_BLUEPRINT}: the {name} service declares no healthCheckPath, so "
                f"a deploy that never finishes starting would still be published."
            )
            continue
        checked += 1
        if _is_readiness_route(path):
            failed = True
            report.fail(
                f"{RENDER_BLUEPRINT}: the {name} health gate points at {path}, a readiness "
                f"route that depends on PostgreSQL and Redis; the gate must use the public "
                f"liveness route {LIVENESS_ROUTE}, or a dependency outage restarts the API."
            )
        elif path != LIVENESS_ROUTE:
            failed = True
            report.fail(
                f"{RENDER_BLUEPRINT}: the {name} health gate points at {path}, not the "
                f"liveness route {LIVENESS_ROUTE}."
            )
        if path not in routes:
            failed = True
            report.fail(
                f"{RENDER_BLUEPRINT}: the {name} health gate probes {path}, which "
                f"{API_MODULE} does not declare as a route; every deploy would fail."
            )
    if not checked and not failed:
        failed = True
        report.fail(
            f"{RENDER_BLUEPRINT}: no service builds from {BACKEND_BUILD_PREFIX}, so the "
            f"API health gate is no longer covered by this check."
        )
    if not failed:
        report.ok(f"{checked} Render health gate(s) probe the declared liveness route")


def _script_location(root: Path) -> str:
    """The versions tree alembic will read, as backend/alembic.ini declares it."""
    for line in _read(root, ALEMBIC_INI).splitlines():
        match = SCRIPT_LOCATION.match(line.strip())
        if match:
            return match.group(1)
    return ""


def _parents(value: str) -> list[str]:
    """down_revision as alembic reads it: None, one id, or several at a merge point."""
    if value.split("#")[0].strip() == "None":
        return []
    return QUOTED.findall(value)


def _revision_graph(versions: Path) -> tuple[dict[str, list[Path]], dict[str, list[str]], list[Path]]:
    """revision id -> files declaring it, revision id -> parents, and files declaring none."""
    revisions: dict[str, list[Path]] = {}
    parents: dict[str, list[str]] = {}
    unparsed: list[Path] = []
    for migration in sorted(versions.glob("*.py")):
        identifier = None
        down: list[str] | None = None
        for line in migration.read_text(encoding="utf-8").splitlines():
            if identifier is None:
                match = REVISION_ID.match(line)
                if match:
                    identifier = match.group(1)
                    continue
            if down is None:
                match = DOWN_REVISION.match(line)
                if match:
                    down = _parents(match.group(1))
        if identifier is None or down is None:
            unparsed.append(migration)
            continue
        revisions.setdefault(identifier, []).append(migration)
        parents[identifier] = down
    return revisions, parents, unparsed


def check_migration_chain(root: Path, report: Report) -> None:
    """`alembic upgrade head` must resolve to exactly one path over every migration.

    Alembic resolves `head` from the revision files alone, so every way that resolution
    breaks is a repository fact -- provable here with no database, no DATABASE_URL and no
    other secret. Two heads or a down_revision naming a revision that no longer exists
    abort the command, which under start.sh's `set -e` means the container never starts.
    The quieter failures matter more: a script_location with no versions directory makes
    `alembic upgrade head` *succeed having applied nothing*, and a revision that head
    cannot reach is simply never run. Both let a release report migrations ready while the
    production schema is not the one the code expects.
    """
    location = _script_location(root)
    if not location:
        report.fail(f"{ALEMBIC_INI} declares no script_location, so alembic finds no migrations.")
        return
    scripts = (root / ALEMBIC_INI).parent / location
    versions = scripts / "versions"
    if not (scripts / "env.py").is_file():
        report.fail(
            f"{ALEMBIC_INI}: script_location '{location}' has no env.py, so "
            f"`alembic upgrade head` cannot run at all."
        )
        return
    if not versions.is_dir():
        report.fail(
            f"{ALEMBIC_INI}: script_location '{location}' has no versions/ directory; "
            f"`alembic upgrade head` would exit 0 having applied nothing."
        )
        return

    revisions, parents, unparsed = _revision_graph(versions)
    if unparsed:
        names = ", ".join(sorted(path.name for path in unparsed))
        report.fail(
            f"{location}/versions: {names} declare no revision/down_revision pair, so "
            f"alembic cannot place them in the chain."
        )
        return
    if not revisions:
        report.fail(
            f"{location}/versions holds no migration, so `alembic upgrade head` would "
            f"exit 0 having applied nothing and the API would start on an empty schema."
        )
        return

    duplicated = {
        identifier: files for identifier, files in revisions.items() if len(files) > 1
    }
    if duplicated:
        detail = "; ".join(
            f"{identifier} in {', '.join(sorted(path.name for path in files))}"
            for identifier, files in sorted(duplicated.items())
        )
        report.fail(f"{location}/versions: duplicate revision id(s): {detail}.")
        return

    dangling = sorted(
        f"{revisions[child][0].name} -> {parent}"
        for child, ancestors in parents.items()
        for parent in ancestors
        if parent not in revisions
    )
    if dangling:
        report.fail(
            f"{location}/versions: down_revision points at a revision that does not "
            f"exist: {'; '.join(dangling)}."
        )
        return

    referenced = {parent for ancestors in parents.values() for parent in ancestors}
    heads = sorted(identifier for identifier in revisions if identifier not in referenced)
    if len(heads) != 1:
        report.fail(
            f"{location}/versions: `alembic upgrade head` needs exactly one head, found "
            f"{len(heads)}: {', '.join(heads) or '(none -- the chain is a cycle)'}."
        )
        return

    reachable: set[str] = set()
    pending = [heads[0]]
    while pending:
        identifier = pending.pop()
        if identifier in reachable:
            continue
        reachable.add(identifier)
        pending.extend(parents[identifier])
    stranded = sorted(set(revisions) - reachable)
    if stranded:
        report.fail(
            f"{location}/versions: {', '.join(stranded)} cannot be reached from head "
            f"{heads[0]}, so `alembic upgrade head` would never apply them."
        )
        return

    report.ok(f"{len(revisions)} migrations form one chain to head {heads[0]}")


def check_migration_entrypoint(root: Path, report: Report) -> None:
    """The release must actually apply migrations, and abort if applying them fails.

    A valid chain proves nothing if the container never runs it, or runs it and keeps
    going after it failed: gunicorn would then serve the old schema while the deploy
    reports success. start.sh is the Render dockerCommand (see render.yaml), so this is
    the one place that decides whether a release applies migrations at all.
    """
    lines = _read(root, BACKEND_ENTRYPOINT).splitlines()
    upgrade_at = next(
        (index for index, line in enumerate(lines) if UPGRADE_TO_HEAD.match(line.strip())),
        None,
    )
    if upgrade_at is None:
        report.fail(
            f"{BACKEND_ENTRYPOINT} never runs `alembic upgrade head`, so a release would "
            f"start the API against whatever schema the database already had."
        )
        return
    abort_at = next(
        (index for index, line in enumerate(lines) if ABORT_ON_ERROR.match(line.strip())),
        None,
    )
    if abort_at is None or abort_at > upgrade_at:
        report.fail(
            f"{BACKEND_ENTRYPOINT} runs `alembic upgrade head` without an earlier "
            f"`set -e`, so a failed migration would not stop the API from starting."
        )
        return
    report.ok(f"{BACKEND_ENTRYPOINT} applies migrations and aborts the boot if they fail")


def _syntax_environment(root: Path, compose_files: list[str], example: str) -> dict[str, str]:
    """Values that let `config` parse a file without any real credential.

    Compose aborts on an unset `${VAR}` and on the fail-closed `${VAR:?message}` form,
    so syntax alone cannot be checked with an empty environment. The committed example
    supplies what it documents; anything it deliberately leaves blank — the opt-in GPU
    command contracts, for instance — gets an obviously synthetic stand-in. Whether
    those values are actually documented is a separate check, not this one.
    """
    environment = dict(os.environ)
    environment.update(env_entries(root, example))
    for compose_file in compose_files:
        required, _ = interpolated_names(root, compose_file)
        for name in required:
            if not environment.get(name):
                environment[name] = SYNTHETIC_CONFIG_VALUE
    return environment


def _compose_config(
    root: Path, compose_files: list[str], environment: dict[str, str], report: Report
) -> None:
    label = " + ".join(compose_files)
    command = ["docker", "compose"]
    for compose_file in compose_files:
        command += ["--file", compose_file]
    command += ["config", "--quiet"]
    try:
        # Fixed argument list, no shell, and no path derived from file content.
        result = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.skip(f"compose syntax: `docker compose config` could not run for {label} ({exc})")
        return
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        report.fail(
            f"{label}: `docker compose config` failed: {detail[-1] if detail else 'no output'}"
        )
        return
    report.ok(f"{label} parses under `docker compose config`")


def check_compose_syntax(root: Path, report: Report) -> None:
    """Validate with the real tool when it is present; say so plainly when it is not."""
    if shutil.which("docker") is None:
        report.skip("compose syntax: docker is unavailable, so `docker compose config` was skipped")
        return

    _compose_config(
        root, [DEV_COMPOSE], _syntax_environment(root, [DEV_COMPOSE], DEV_ENV_EXAMPLE), report
    )
    # The spatial file is an overlay: it extends services defined in the base file and
    # cannot be parsed alone, which is how it has to be validated.
    overlay = [DEV_COMPOSE, SPATIAL_COMPOSE]
    _compose_config(root, overlay, _syntax_environment(root, overlay, DEV_ENV_EXAMPLE), report)

    # The production services load `.env.production`. That file holds real credentials
    # and is not committed, and compose refuses to parse without it, so the committed
    # example stands in for the duration of the check. An existing file is never touched
    # and never read: if one is present, it is already the operator's own.
    production = [PRODUCTION_COMPOSE]
    environment = _syntax_environment(root, production, PRODUCTION_ENV_EXAMPLE)
    real_env = root / ".env.production"
    if real_env.exists():
        _compose_config(root, production, environment, report)
        return
    try:
        real_env.write_text(_read(root, PRODUCTION_ENV_EXAMPLE), encoding="utf-8")
        _compose_config(root, production, environment, report)
    finally:
        real_env.unlink(missing_ok=True)


def run_preflight(root: Path) -> Report:
    report = Report()
    check_documented_variables(root, report)
    check_production_configuration(root, report)
    check_example_holds_no_secrets(root, report)
    check_env_files_have_examples(root, report)
    check_health_probes(root, report)
    check_health_dependencies(root, report)
    check_reverse_proxy_contract(root, report)
    check_workflow_probe_paths(root, report)
    check_render_health_gate(root, report)
    check_migration_chain(root, report)
    check_migration_entrypoint(root, report)
    check_compose_syntax(root, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the release wiring without secrets.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    report = run_preflight(args.root.resolve())
    for message in report.passed:
        print(f"  ok      {message}")
    for message in report.skips:
        print(f"  skipped {message}")
    for message in report.failures:
        print(f"  FAIL    {message}")
    if report.failures:
        print(f"\nRelease preflight failed with {len(report.failures)} problem(s).")
        return 1
    print(
        "\nRelease preflight passed. Actual secret values, DNS, TLS certificates and "
        "managed-service reachability are still verified on the deployment host."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
