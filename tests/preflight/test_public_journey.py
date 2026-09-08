"""Can a person who just registered actually reach the product?

The advertised journey is register -> project -> upload -> render -> download.
Every leg after the first is scoped to a project: `/media/multipart-upload/*`
takes a `project_id` and 404s when it does not exist, renders are requested on a
timeline that belongs to a project, and a download URL is authorised through
`job.project.owner_id`. So the journey has a precondition nothing else tests:
**a registered user must be able to obtain a project.**

For a while they could not. Across the 195 routes in `backend/app/api/v1`, 120
of them POST, none created a `Project`: the only construction site in the whole
backend was a Celery task behind the headless Platform API, whose keys are
issued only to a caller holding `X-Platform-Admin-Token`. Two checks here were
`xfail(strict=True)` while that was true, so they would fail the moment it
stopped being true and ask to be un-marked.

`POST /api/v1/projects` closed it, and the markers came off with it. What these
checks now hold is the shape of the fix rather than the shape of the gap: the
precondition still exists, the route that satisfies it is authenticated and
scoped to its caller, and the studio actually passes an id to the media bin.
The failure they are written against is a regression that silently returns the
journey to local-only -- which is exactly how it looked before, since nothing
errored then either.

The checks are static: they parse the route modules and read the frontend entry
point. No database, no network, no credential, no running service -- so they
belong in the preflight suite and run on every pull request.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "backend" / "app" / "api" / "v1"
BACKEND_APP = ROOT / "backend" / "app"
PLATFORM_MODULE = API_DIR / "platform.py"
MEDIA_MODULE = API_DIR / "media.py"
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


def test_a_registered_user_can_create_a_project() -> None:
    """The leg that was missing. A POST route must produce the object.

    Written against the route surface rather than a path literal so renaming
    the endpoint is allowed and deleting the capability is not.
    """

    creating = [
        f"{method} {path}"
        for method, path, node in _all_route_handlers()
        if method == "POST" and _constructs_a_project(node)
    ]
    assert creating, "no POST route in backend/app/api/v1 constructs a Project"


def test_the_creation_route_is_authenticated_and_owner_scoped() -> None:
    """A new write surface that mints an upload target has two ways to be wrong.

    It could create projects for anonymous callers, or take the owner from the
    request body -- which this codebase still does elsewhere, so it is a live
    habit and not a hypothetical (see the SPOOFABLE_USER_ID route map). Either
    would hand a stranger an upload path into someone else's storage, which is
    worse than the gap this route was added to close.
    """

    creators = [
        node
        for method, _path, node in _all_route_handlers()
        if method == "POST" and _constructs_a_project(node)
    ]
    assert creators, "no creation route to check"

    for node in creators:
        source = ast.unparse(node)
        with_subtest = f"{node.name}: "
        assert "get_current_user" in source, (
            with_subtest + "the creation route does not require an authenticated caller"
        )
        assert "owner_id=current_user.id" in source, (
            with_subtest + "the owner is not taken from the verified session"
        )
        assert "owner_id=payload" not in source and "owner_id=request" not in source, (
            with_subtest + "the owner is being read from the request"
        )


def test_the_listing_route_filters_on_the_caller() -> None:
    """Listing is how the studio avoids minting a workspace per page load.

    Filtering in the query rather than after the fact is what keeps another
    person's project invisible instead of merely refused.
    """

    listings = [
        node
        for method, path, node in _all_route_handlers()
        if method == "GET" and path in {"", "/"} and "Project" in ast.unparse(node)
    ]
    assert listings, "no project listing route found"
    for node in listings:
        source = ast.unparse(node)
        assert "get_current_user" in source
        assert "Project.owner_id == current_user.id" in source


def test_the_admin_path_is_still_admin_gated() -> None:
    """The pre-existing Platform API route must not have been loosened.

    Its keys are the one way to create a project on someone else's behalf, and
    adding a user-facing route is no reason to relax that.
    """

    platform = PLATFORM_MODULE.read_text(encoding="utf-8")
    issuing = platform.split('@router.post("/api-keys"', 1)[1].split("@router.")[0]
    assert "require_platform_management_token" in issuing
    assert "X-Platform-Admin-Token" in platform


def test_every_project_construction_site_is_accounted_for() -> None:
    """A new construction site is a new way to own a project. Name them all.

    Not a style rule: each entry here is a path by which a `Project` row comes
    into existence, and an unreviewed fourth one is exactly how an
    unauthenticated or mis-scoped creation path would arrive.
    """

    assert set(_project_construction_sites()) == {
        # The headless Platform API, behind X-Platform-Admin-Token.
        "backend/app/tasks/platform_tasks.py:82",
        # The user-facing route this journey depends on.
        "backend/app/api/v1/projects.py:57",
    }, f"unreviewed Project construction sites: {_project_construction_sites()}"


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


def test_the_studio_entry_supplies_a_project_to_the_media_bin() -> None:
    """The browser half of the fix, and the half that fails silently.

    `LocalMediaBin` marks every file `local` when it has no project id, and the
    workspace shows 「雲端同步會在背景完成」 -- cloud sync completes in the
    background. Dropping the prop again would restore that mismatch without
    erroring anywhere, which is precisely how it went unnoticed the first time.
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
