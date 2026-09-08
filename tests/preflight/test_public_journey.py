"""Can a person who just registered actually reach the product?

The advertised journey is register -> project -> upload -> render -> download.
Every leg after the first is scoped to a project: `/media/multipart-upload/*`
takes a `project_id` and 404s when it does not exist, renders are requested on a
timeline that belongs to a project, and a download URL is authorised through
`job.project.owner_id`. So the journey has a precondition nothing else tests:
**a registered user must be able to obtain a project.**

They cannot. Across the 195 routes in `backend/app/api/v1`, 120 of them POST,
none creates a `Project`. The single construction site in the whole backend is
a Celery task behind the headless Platform API, whose keys are issued only to a
caller holding `X-Platform-Admin-Token`. The repository's own QA fixture
(`tests/qa/create_render_fixture.py`) does not use the API at all -- it opens a
`SessionLocal` and inserts the row directly, which is the clearest evidence
that the gap is real rather than a naming accident.

The checks below are static: they parse the route modules and read the frontend
entry point. No database, no network, no credential, no running service -- so
they belong in the preflight suite and run on every pull request.

## Why two of them are `xfail(strict=True)`

Those two assert the behaviour the product needs, and fail today. Marked strict
so they stay quiet while the gap is open and fail loudly the moment it closes --
"unexpected success" is the signal to delete the marker, not to relax the test.
A test that asserted the *current* broken state instead would go green and then
quietly defend the bug against being fixed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "backend" / "app" / "api" / "v1"
BACKEND_APP = ROOT / "backend" / "app"
PLATFORM_MODULE = API_DIR / "platform.py"
MEDIA_MODULE = API_DIR / "media.py"
QA_FIXTURE = ROOT / "tests" / "qa" / "create_render_fixture.py"
STUDIO_LAUNCHPAD = ROOT / "frontend" / "features" / "onboarding" / "studio-launchpad.tsx"
MEDIA_BIN = ROOT / "frontend" / "features" / "media" / "local-media-bin.tsx"
WORKSPACE = ROOT / "frontend" / "features" / "workspace" / "adaptive-editor-workspace.tsx"

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _route_handlers(path: Path) -> list[tuple[str, str, ast.AST]]:
    """(method, path, function) for every routed handler in a module."""

    found: list[tuple[str, str, ast.AST]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in HTTP_METHODS
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                found.append((decorator.func.attr.upper(), decorator.args[0].value, node))
    return found


def _all_route_handlers() -> list[tuple[str, str, ast.AST]]:
    return [entry for module in sorted(API_DIR.glob("*.py")) for entry in _route_handlers(module)]


def _constructs_a_project(node: ast.AST) -> bool:
    """Whether a function body instantiates the `Project` ORM model."""

    return any(
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id == "Project"
        for inner in ast.walk(node)
    )


def _project_construction_sites() -> list[str]:
    """Every `Project(...)` instantiation in the backend, as `path:line`."""

    sites: list[str] = []
    for path in sorted(BACKEND_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Project":
                sites.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    return sites


# --------------------------------------------------------------------------- #
# The precondition
# --------------------------------------------------------------------------- #


def test_the_route_surface_is_readable() -> None:
    """Guards every check below: an unreadable surface must not pass vacuously."""

    handlers = _all_route_handlers()
    assert len(handlers) > 100, f"only {len(handlers)} routes parsed; the reader is broken"
    assert any(method == "POST" for method, _, _ in handlers)


def test_uploading_requires_a_project_that_already_exists() -> None:
    """The first leg after registration depends on a project it cannot create.

    `_create_uploading_asset` looks the project up and 404s when it is absent,
    so every upload entry point inherits that precondition.
    """

    source = MEDIA_MODULE.read_text(encoding="utf-8")
    creator = source.split("def _create_uploading_asset", 1)[1].split("\ndef ")[0]
    assert "db.get(Project, payload.project_id)" in creator
    assert "Project not found" in creator

    upload_paths = {
        path
        for method, path, node in _route_handlers(MEDIA_MODULE)
        if method == "POST" and "_create_uploading_asset" in ast.unparse(node)
    }
    assert upload_paths, "no upload entry point calls _create_uploading_asset any more"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "No HTTP route creates a Project, so a registered user cannot start the "
        "advertised journey. Delete this marker with the route that fixes it."
    ),
)
def test_a_registered_user_can_create_a_project() -> None:
    """The missing leg. Nothing in the public API creates a project.

    This is the blocker: 120 POST routes, none of which produces the one object
    every later leg requires.
    """

    creating = [
        f"{method} {path}"
        for method, path, node in _all_route_handlers()
        if method == "POST" and _constructs_a_project(node)
    ]
    assert creating, "no POST route in backend/app/api/v1 constructs a Project"


def test_the_only_project_creation_path_is_admin_gated() -> None:
    """Pins *why* the check above fails, so the diagnosis cannot drift silently.

    If a second construction site appears, this fails and the reason recorded
    above has to be re-derived rather than assumed still true.
    """

    sites = _project_construction_sites()
    assert sites == ["backend/app/tasks/platform_tasks.py:82"], (
        f"Project is now constructed at {sites}; re-check whether a user-reachable "
        f"path exists before trusting the blocker above"
    )

    # And the keys that reach that task are issued only to an admin caller.
    platform = PLATFORM_MODULE.read_text(encoding="utf-8")
    issuing = platform.split('@router.post("/api-keys"', 1)[1].split("@router.")[0]
    assert "require_platform_management_token" in issuing
    assert "X-Platform-Admin-Token" in platform


def test_the_repositorys_own_fixture_bypasses_the_api_to_get_a_project() -> None:
    """Corroboration from the project's own tooling.

    The QA render fixture does not call the API to create a project; it opens a
    database session and inserts the row. Tooling routing around an endpoint is
    good evidence the endpoint is missing rather than merely differently named.
    """

    fixture = QA_FIXTURE.read_text(encoding="utf-8")
    assert "SessionLocal" in fixture
    assert re.search(r"Project\(owner_id=", fixture)


# --------------------------------------------------------------------------- #
# The same gap, seen from the browser
# --------------------------------------------------------------------------- #


def test_the_media_bin_only_uploads_when_it_is_given_a_project() -> None:
    """The frontend half of the precondition, read from the component itself."""

    bin_source = MEDIA_BIN.read_text(encoding="utf-8")
    assert "if (projectId) void uploadToProject(" in bin_source, (
        "the media bin no longer gates uploading on a project id; re-read the "
        "flow before trusting the check below"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The studio entry renders the workspace with no project id, so the media "
        "bin can never upload -- while the UI promises background cloud sync."
    ),
)
def test_the_studio_entry_supplies_a_project_to_the_media_bin() -> None:
    """The UI advertises a sync that cannot happen.

    `LocalMediaBin` shows 「雲端同步會在背景完成」 -- cloud sync completes in the
    background -- and marks every file `local` when it has no project id.
    `StudioLaunchpad` renders the workspace without one, so that promise is
    unkeepable for every visitor, not merely unimplemented.
    """

    assert "雲端同步會在背景完成" in WORKSPACE.read_text(encoding="utf-8"), (
        "the cloud-sync promise was removed from the workspace; this check is "
        "about the mismatch between that promise and the wiring, so re-read "
        "both before editing"
    )
    launchpad = STUDIO_LAUNCHPAD.read_text(encoding="utf-8")
    assert re.search(r"<AdaptiveEditorWorkspace[^>]*projectId=", launchpad), (
        "StudioLaunchpad renders AdaptiveEditorWorkspace without a projectId, so "
        "LocalMediaBin never calls uploadToProject"
    )
