# VantaCut — Render deployment path

`docs/launch-readiness.md` describes the self-hosted Docker Compose + Nginx
topology and still says the product has no user sign-in flow. Both statements
are out of date. This document covers what is actually deployed, and what
remains between that and calling it public.

## What is live right now

Verified 2026-08-27 by unauthenticated probe only. No account was created and
no credential was used.

| Piece | URL | Evidence |
| --- | --- | --- |
| API + Celery render worker (one free-tier container) | `https://vantacut-backend.onrender.com` | `/health` 200, `/ready` 200 — so PostgreSQL *and* Redis both answer |
| Frontend (`next build`, not `next dev`) | `https://vantacut-frontend.onrender.com` | `x-nextjs-prerender: 1`, `x-nextjs-cache: HIT`, no react-refresh in the HTML |
| `/studio` | `https://vantacut-frontend.onrender.com/studio` | renders the real sign-in gate; `AuthGate` is in force |
| API surface | — | `/openapi.json` lists 176 paths, 170 operations with explicit `security` |

Two things that are frequently the blocker on a deployment like this are already
correct and were checked, not assumed:

- **CORS.** A preflight to `/api/v1/auth/login` with
  `Origin: https://vantacut-frontend.onrender.com` returns 200 with
  `access-control-allow-origin` echoing that exact origin. `CORS_ALLOWED_ORIGINS`
  is set correctly on the live service.
- **The frontend really points at the API.** `https://vantacut-backend.onrender.com`
  is inlined in the shipped `/studio` chunk, so `NEXT_PUBLIC_API_URL` was present
  at build time. (It is a build ARG in `frontend/Dockerfile.production`, not a
  runtime variable — setting it after the fact does nothing until a rebuild.)

## Blocker 1 — object storage is unverified, and nothing surfaces that

`S3_ENDPOINT_URL`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` default to a local MinIO
(`http://localhost:9000`, `minioadmin`/`minioadmin123`) in `app/core/config.py`.
`/ready` checks PostgreSQL and Redis and nothing else, so a deployment that never
configured S3 answers `/health` **and** `/ready` with 200 while failing every
single upload, preview and export — every media path in the API goes through
`app/services/storage.py`.

`GET /ready/storage` (added alongside this document) closes that gap. It returns
booleans only — no endpoint URL, bucket name or credential — so it is safe to
call unauthenticated:

```json
{"configured": true, "bucket_reachable": true, "uploads_expected_to_work": true}
```

`configured: false` means `S3_ENDPOINT_URL` is still the development default.
It is deliberately **not** wired into `/ready`: `/ready` is what a load balancer
polls, and widening it would turn a storage outage into a whole-service outage.

Run it against the live service after deploying this change. If
`uploads_expected_to_work` is false, the product cannot do its one job and no
amount of green `/health` says otherwise.

## Blocker 2 — `ENVIRONMENT` is probably still unset, and that is load-bearing

`app/core/config.py` reads `ENVIRONMENT` with a default of `"development"`, and
two things hang off it:

- `Settings.use_mock_ai` is true whenever the environment is `development` or
  `test`. Every AI feature then returns canned output instead of a real result.
- The module-level guard `if settings.environment not in {"development", "test"}
  and not settings.jwt_secret_key: raise RuntimeError(...)` only fires when the
  environment is *not* development. `jwt_secret_key` itself defaults to `""`.

So an unset `ENVIRONMENT` means the API starts happily with an empty JWT signing
key, and an empty HS256 key signs and verifies perfectly well — anyone who knows
that could mint a token for any account.

**This could not be determined from outside the service.** 170 of 176 operations
require auth, `/docs` is not gated on the environment, and no unauthenticated
endpoint reveals mock-AI state, so there is no honest probe for it. Checking is
an owner action: read the Render environment tab. If `ENVIRONMENT` is unset or
`development`, treat it as a live incident rather than a configuration tidy-up.

Ordering matters: setting `ENVIRONMENT=production` **fails the service closed**
until `JWT_SECRET_KEY` is a real value, because that is what the guard is for.
Set the secret first, then the environment, then redeploy.

## `render.yaml` is documentation, not automation

Render reads a blueprint only when an owner syncs it from the dashboard, so
committing `render.yaml` changes nothing about the running services. PostgreSQL
and Redis are deliberately not declared in it — the live services use externally
managed instances and declaring them would ask Render to provision new ones.

The blueprint also records the free-tier shape that is easy to lose: one
container runs both gunicorn and the Celery render worker, and `backend/start.sh`
starts them *staggered* (gunicorn first, worker only once `/health` answers)
because both cold-import the same very large ML stack and 0.1 CPU cannot do that
twice at once without Render's port scan timing out.

## Remaining gap

The authenticated end-to-end chain — register, sign in, upload real footage,
watch the render worker produce an artifact, download it — has never been run.
Worker liveness is confirmed from container logs and `/ready` proves PostgreSQL
and Redis are reachable, but "a real job produced a real artifact" is not
something this repo can evidence. It needs an owner-held session; creating
accounts and entering passwords is outside what the assistant does.
