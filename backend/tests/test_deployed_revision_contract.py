"""The deployed revision must stay observable and must never leak configuration.

Nothing this API serves says which build is running. `/health` returns a
literal, `/ready` reports dependencies, `/ready/storage` reports booleans about
configuration -- none distinguishes one deploy from the next. The last time it
was established that a particular pull request had gone live, the evidence was
`/ready/storage` gaining a new field. That works once per release, and not at
all for a release that changes nothing observable, which is the release where
the question actually gets asked.

`GET /version` answers it. Two properties are worth stating as tests, because
neither is visible in the handler:

1. **Only hexadecimal can be published.** The route is unauthenticated, and an
   environment variable's failure mode is holding the wrong thing -- a database
   URL, an S3 secret key, a pasted `.env` line. A presence or length check
   would return every one of those to an anonymous caller. `/ready/storage`
   draws this line as "booleans only, deliberately"; here the value is not a
   boolean, so the whitelist does the same job.

2. **The three existing contracts did not move.** `/version` is a fourth route
   rather than a field on any of them precisely because each is already read by
   something: the Render health gate and the compose healthcheck probe
   `/health`, `scripts/release_preflight.py` pins `/health` and `/ready` as
   required routes, and `/ready/storage`'s four booleans are what tell an
   operator whether uploads can work. The last group pins all three payloads.

`app/main.py` is not importable in this CI slice -- it pulls in ~75 routers and
their ML stack, while the slice installs a curated dependency set (see
`tests/conftest.py`). So the handler is exercised by extracting its real source
from `app/main.py` and mounting it on a throwaway FastAPI app: the code under
test is the code that ships, without the import weight. The same
load-by-file-path reasoning `conftest.py` applies to `auth.py` and
`test_storage_readiness.py` applies to `config.py`.

No network request is made, no database is opened, and no credential appears in
any assertion.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.revision import (
    REVISION_ENV_VAR,
    commit_sha_or_none,
    deployed_revision,
)

VERSION_ROUTE = "/version"

#: A real 40-character SHA-1, in the shape Render injects.
A_COMMIT_SHA = "596a4931bd7e0c85f2a4d61e93c7b0284fa5de17"

API_MODULE = Path(__file__).resolve().parents[1] / "app" / "main.py"
DEPLOYMENT_DOC = Path(__file__).resolve().parents[2] / "docs" / "render-deployment.md"

#: What an environment variable holds when someone fills in the wrong dashboard
#: box. Every one is truthy, so a presence check would publish all of them.
NOT_A_COMMIT_SHA = [
    "postgresql://vantacut:hunter2@db.internal.example:5432/vantacut",
    "rediss://default:s3cr3t@redis.example.com:6379",
    "minioadmin123",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.9f7Qn0",
    "RENDER_GIT_COMMIT=596a493",
    "https://vantacut-backend.onrender.com",
    "refs/heads/main",
    "main",
    "v0.1.0",
    "unknown",
    "$RENDER_GIT_COMMIT",
    "",
    "   ",
]


def _handler(route: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The real handler for `route`, parsed out of app/main.py by decorator."""

    module = ast.parse(API_MODULE.read_text(encoding="utf-8"))
    handler = next(
        (
            node
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(decorator, ast.Call)
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and decorator.args[0].value == route
                for decorator in node.decorator_list
            )
        ),
        None,
    )
    assert handler is not None, f"{route} is no longer declared in app/main.py"
    return handler


def _returned_dict(route: str) -> dict[str, str]:
    """Each field `route` returns, mapped to the source that produces it.

    Source text, not values: what a field would resolve to at runtime is never
    read here, so this stays safe against a payload that reads a secret.
    """

    returned = next(
        (node.value for node in ast.walk(_handler(route)) if isinstance(node, ast.Return)),
        None,
    )
    assert isinstance(returned, ast.Dict), f"{route} no longer returns a dict literal"
    return {
        (key.value if isinstance(key, ast.Constant) else ast.unparse(key)): ast.unparse(value)
        for key, value in zip(returned.keys, returned.values)
    }


@pytest.fixture
def version_client() -> TestClient:
    """The shipped `/version` handler, mounted alone on a throwaway app.

    `app.main` cannot be imported here (see the module docstring), so the
    handler's own source is taken from the real file and executed with the one
    name it closes over. Testing the extracted body rather than a re-typed copy
    is the point: a change to the handler changes what these tests exercise.
    """

    handler = _handler(VERSION_ROUTE)
    # The decorator names the `app` object in main.py, which does not exist
    # here; dropping it leaves the function body -- the part under test --
    # untouched, and it is re-registered on this app below.
    handler.decorator_list = []

    namespace: dict[str, object] = {"deployed_revision": deployed_revision}
    exec(ast.unparse(handler), namespace)  # noqa: S102 - the shipped source, read above

    app = FastAPI()
    app.get(VERSION_ROUTE)(namespace[handler.name])
    return TestClient(app)


# --------------------------------------------------------------------------- #
# What the route answers
# --------------------------------------------------------------------------- #


def test_it_reports_the_commit_the_platform_injected(
    version_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point, over real HTTP rather than by calling a function."""

    monkeypatch.setenv(REVISION_ENV_VAR, A_COMMIT_SHA)
    response = version_client.get(VERSION_ROUTE)

    assert response.status_code == 200
    assert response.json() == {"revision": A_COMMIT_SHA}


def test_it_reports_null_when_the_platform_injected_nothing(
    version_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every local run, and every host that is not Render, is in this state.

    `null` rather than a 500 or an invented string: an unknown revision is
    normal, and the route still has to answer.
    """

    monkeypatch.delenv(REVISION_ENV_VAR, raising=False)
    response = version_client.get(VERSION_ROUTE)

    assert response.status_code == 200
    assert response.json() == {"revision": None}


def test_the_payload_carries_the_revision_and_no_other_field(
    version_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A route answering "what is deployed?" invites more fields.

    The environment name, the bucket, a provider flag: all configuration, all
    on an unauthenticated route. Pinning the key set is what stops the next
    useful-sounding addition from being published to anyone who asks.
    """

    monkeypatch.setenv(REVISION_ENV_VAR, A_COMMIT_SHA)
    assert set(version_client.get(VERSION_ROUTE).json()) == {"revision"}


def test_the_route_takes_no_dependency() -> None:
    """It reports the build, so it must answer while a dependency is down.

    `/ready` opens PostgreSQL and Redis and 503s when either is unreachable.
    A parameter on this handler is what would put FastAPI's injection -- a
    database session, an auth gate -- in front of the one route whose whole job
    is to be answerable during exactly that incident.
    """

    handler = _handler(VERSION_ROUTE)
    assert handler.args.args == []
    assert handler.args.kwonlyargs == []


# --------------------------------------------------------------------------- #
# What may be published
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", NOT_A_COMMIT_SHA)
def test_a_value_that_is_not_a_commit_sha_is_never_published(
    version_client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The leak guard, driven through the route rather than the parser.

    Each of these is truthy, so a presence check -- the shape this repository
    has already had to correct once, in the storage readiness endpoint -- would
    hand every one of them to an anonymous caller.
    """

    monkeypatch.setenv(REVISION_ENV_VAR, value)
    assert version_client.get(VERSION_ROUTE).json() == {"revision": None}


def test_the_parser_accepts_a_real_sha() -> None:
    """Guards the guard: prove the rejections above are a filter, not a wall.

    Without this, `commit_sha_or_none` could return `None` for every input and
    satisfy every rejection check in this file.
    """

    assert commit_sha_or_none(A_COMMIT_SHA) == A_COMMIT_SHA


def test_the_parser_normalizes_case_and_surrounding_whitespace() -> None:
    """A pasted value arrives with a newline; some tools print SHAs uppercase.

    Both name the same commit, so both stay usable -- but one published form
    means two probes of one deployment cannot disagree.
    """

    assert commit_sha_or_none(f"  {A_COMMIT_SHA.upper()}\n") == A_COMMIT_SHA


@pytest.mark.parametrize(
    "value",
    [
        A_COMMIT_SHA[:6],
        A_COMMIT_SHA + "0",
        A_COMMIT_SHA[:-1] + "g",
        A_COMMIT_SHA[:20] + " " + A_COMMIT_SHA[21:],
    ],
    ids=["too-short", "too-long", "non-hex-character", "embedded-space"],
)
def test_the_parser_rejects_near_misses(value: str) -> None:
    """Anchored, not searched: a SHA inside a longer string is not a SHA.

    `A_COMMIT_SHA + "0"` is the case that matters -- an unanchored pattern
    matches the leading 40 characters and publishes a value the platform never
    set, which is worse than publishing nothing at all.
    """

    assert commit_sha_or_none(value) is None


def test_an_abbreviated_sha_is_accepted() -> None:
    """Render sends 40 characters; a hand-set value on another host may not.

    Seven is git's own abbreviation floor, and length does not weaken the
    property being defended: it is hexadecimal or nothing either way.
    """

    assert commit_sha_or_none(A_COMMIT_SHA[:7]) == A_COMMIT_SHA[:7]


def test_the_environment_read_uses_the_variable_render_actually_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing else would notice this being wrong.

    A module reading an unset variable returns `None`, which is also what a
    correct module returns on every host that is not Render -- so a typo here
    looks exactly like a correct local run.
    """

    assert REVISION_ENV_VAR == "RENDER_GIT_COMMIT"
    monkeypatch.setenv("RENDER_GIT_COMMIT", A_COMMIT_SHA)
    assert deployed_revision() == A_COMMIT_SHA


def test_the_route_is_wired_to_the_validating_reader() -> None:
    """The handler must publish the parsed value, not the raw variable.

    Every leak test above runs against the extracted handler, so a handler that
    read `os.environ` directly would fail them -- but only while this file
    keeps extracting the real source. Stating the expression makes the
    dependency explicit rather than incidental.
    """

    assert _returned_dict(VERSION_ROUTE) == {"revision": "deployed_revision()"}


# --------------------------------------------------------------------------- #
# The three contracts this route exists to leave alone
# --------------------------------------------------------------------------- #


def test_the_liveness_payload_is_unchanged() -> None:
    """Both the Render health gate and the compose healthcheck probe /health."""

    assert _returned_dict("/health") == {"status": "'ok'"}


def test_the_readiness_payload_is_unchanged() -> None:
    """`/ready` is what a load balancer drains on; its body is a literal too."""

    assert _returned_dict("/ready") == {"status": "'ready'"}


def test_the_storage_readiness_route_still_delegates_unchanged() -> None:
    """Its four booleans are the upload diagnostic, and they are not ours.

    `/ready/storage` returns `storage_readiness()` whole, so any revision field
    added there would have to be added to that function's dict -- which
    `test_storage_readiness.py::test_result_is_booleans_only` would then fail.
    Pinning the delegation keeps that chain intact.
    """

    handler = _handler("/ready/storage")
    returned = next(node for node in ast.walk(handler) if isinstance(node, ast.Return))
    assert ast.unparse(returned.value) == "storage_readiness()"


@pytest.mark.parametrize("route", ["/health", "/ready", "/ready/storage"])
def test_no_existing_route_reports_the_revision(route: str) -> None:
    """Stated directly, so it fails here rather than in review.

    Each of these payloads is read by something that would not notice a new
    field until it mattered: a health gate, a load balancer, an operator
    diagnosing uploads. `/version` exists so none of them has to change.
    """

    assert "revision" not in ast.unparse(_handler(route))


# --------------------------------------------------------------------------- #
# Documentation
# --------------------------------------------------------------------------- #


def test_the_deployment_doc_explains_how_to_read_the_route() -> None:
    """Three answers, each meaning something different; 404 is the subtle one.

    Without the doc a 404 reads as "broken" rather than "the deployed build
    predates this route", which is the most useful thing the route can say to
    an operator chasing a deploy that did not land.
    """

    doc = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    assert VERSION_ROUTE in doc
    assert REVISION_ENV_VAR in doc
    assert "404" in doc
