"""The commit this process was built from, validated before it is published.

Nothing this API serves says which build is running. `/health` returns a
literal, `/ready` reports its dependencies, and `/ready/storage` reports
booleans about configuration -- none of them distinguishes one deploy from the
next. The only evidence that a merge actually reached the service has been
behavioural: `/ready/storage` gaining `public_endpoint_configured` is how it
was last established that a particular pull request was live. That works once
per release, and not at all for a release that changes nothing observable --
which is exactly the release where the question gets asked.

Render sets `RENDER_GIT_COMMIT` on every deploy, at build time and at runtime.
This module reads that one variable and nothing else in the environment.

Deliberately standalone rather than a field on `app.core.config.Settings`:

* `Settings` evaluates its class body once at import, so a value read there is
  frozen for the process and can only be exercised in tests by re-executing the
  module (see `test_storage_readiness.py`). Reading here, per call, keeps the
  tests direct and costs nothing -- the variable cannot change without a
  restart anyway.
* Importing `app.main` is impossible in the CI backend slice, which installs a
  curated dependency set rather than the ML stack the API's ~75 routers pull
  in. A small module with no project imports stays testable there.

The route that serves this is unauthenticated, like `/health`. That makes the
parse below a **whitelist**: a value is published only when it already is a
commit SHA, normalized rather than echoed as typed. Whatever ends up in that
variable -- a database URL, an S3 secret key, a whole `.env` line pasted into
the wrong dashboard box -- the only characters this module can emit are
hexadecimal digits. `/ready/storage` draws the same line with "booleans only,
deliberately"; this is that rule applied to a value that is not a boolean.

A rejected value is not logged either. The reason to refuse it is that it might
be a secret, so writing it to the log would move the leak rather than close it.
"""

from __future__ import annotations

import os
import re

#: Render sets this on every deploy. It is the only variable this module reads.
REVISION_ENV_VAR = "RENDER_GIT_COMMIT"

#: A commit SHA, and nothing that is not one. The lower bound is git's own
#: abbreviation floor, so a short SHA set by hand on a host that is not Render
#: stays usable; the upper bound is a full SHA-1. Anchored on both ends,
#: because an unanchored pattern would find a SHA inside a longer string and
#: publish a value the platform never set -- worse than publishing nothing.
_COMMIT_SHA = re.compile(r"\A[0-9a-fA-F]{7,40}\Z")


def commit_sha_or_none(value: str | None) -> str | None:
    """`value` as a normalized commit SHA, or ``None`` when it is not one.

    Pure, and separate from the environment read below, so what may be
    published can be tested against inputs a process environment is awkward to
    hold.
    """
    if value is None:
        return None

    candidate = value.strip()
    if not _COMMIT_SHA.match(candidate):
        return None

    return candidate.lower()


def deployed_revision() -> str | None:
    """The commit this process was built from, or ``None`` if it is unknown.

    ``None`` covers two situations and deliberately does not tell them apart to
    the caller: the variable is unset -- every local run and every host that is
    not Render -- or it holds something that is not a commit SHA. Reporting
    which would mean reporting something about a value that may be a secret.
    """
    return commit_sha_or_none(os.getenv(REVISION_ENV_VAR))
