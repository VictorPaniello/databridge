# databridge

A small service that does what a Forward Deployed Engineer does on day one
at a new client: take their messy data export, clean it, get it into a real
database, and notify another system when something new arrives.

Concretely: upload a CSV/Excel file → it's cleaned and validated via
[tidycsv](https://github.com/VictorPaniello/tidycsv) → every row is
persisted to PostgreSQL → a webhook fires for each newly ingested record,
with every delivery attempt logged (success or failure) for auditability.

## Why this exists

`tidycsv` solves "the client's data is messy." This project solves the next
problem: "now get that data into a system, and tell another system about
it" — the two things that show up over and over in Forward Deployed
Engineer job postings (Juryo, Flyboard, ElevenLabs, Valerdat, mafer AI):
connect to what the client already has (a CRM, an ERP, a spreadsheet
export), and own the integration end to end.

It reuses `tidycsv` as a real dependency (`pip install`-ed from its GitHub
repo), not by copy-pasting its logic — and finding and fixing several real
bugs along the way, in the code and in deploying it, is part of the story,
not something to hide (see
[Bugs found while building this](#bugs-found-while-building-this) below).

## API

Every `/records*` endpoint requires a `Bearer` token (see
[Authentication](#authentication)) and only ever returns the calling
engineer's own client records - not because each call filters by a
company/client parameter, but because each `ClientRecord` has an
`owner_id`, so "my clients" is just "records where `owner_id` is me".

| Method | Path | What it does |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/auth/register` | Create an account (email + password) |
| `POST` | `/auth/jwt/login` | Log in, get back a bearer token |
| `GET` | `/auth/github/authorize` | Start "Sign in with GitHub" (only present if `GITHUB_CLIENT_ID`/`SECRET` are set) |
| `GET` | `/users/me` | The logged-in engineer's own profile |
| `DELETE` | `/users/me` | Permanently erase **your own** account and everything it owns (client records, ingestion runs, webhook deliveries, linked OAuth account) - real self-service GDPR erasure, not a request queue |
| `POST` | `/records/upload` | Upload a CSV/Excel file, clean + persist it (tagged to the caller), fire webhooks for new records - returns an `ingestion_run_id` |
| `GET` | `/ingestion-runs` | List **your own** past uploads, paginated - the persisted summary of every upload, not just the one the last `IngestResult` response reported |
| `GET` | `/ingestion-runs/{id}` | Fetch one of **your own** past uploads - 404 (not 403) otherwise |
| `GET` | `/records` | List **your own** records, paginated (`?limit=&offset=`, `limit` capped server-side at 500), optionally `?has_issues=true/false` and/or `?ingestion_run_id=` (drill from one upload into exactly the records it created); returns `{items, total, limit, offset}` |
| `GET` | `/records/{id}` | Fetch one of **your own** records - 404 (not 403) if it belongs to someone else, or doesn't exist |
| `GET` | `/records/{id}/webhooks` | Audit log of webhook delivery attempts for one of your own records |
| `POST` | `/records/{id}/webhooks/replay` | Manually re-send the notification for one of your own records, on demand - 400 if no `WEBHOOK_URL` is configured |
| `DELETE` | `/records/{id}` | Permanently erase one of your own records (and its webhook delivery history) - supports the GDPR right to erasure, not a standalone claim of full GDPR compliance on its own; see [Privacy Policy](#privacy--terms) |

Re-uploading a file already ingested (matched by email, scoped to the
uploading engineer) is a no-op, not a duplicate insert or an error - two
different engineers uploading a client with the same email are two
separate records, not duplicates of each other.

## Authentication

Built on [fastapi-users](https://fastapi-users.github.io/fastapi-users/)
rather than hand-rolled password hashing/JWT/OAuth - real production
systems don't reinvent this. Two ways in, both landing on the same kind of
account:

- **Email + password**: `POST /auth/register` (now also requires
  `first_name`/`last_name`; `phone` is optional), then
  `POST /auth/jwt/login` (form-encoded `username`/`password`) for a
  bearer token in the response body
- **GitHub OAuth** (optional - only enabled when `GITHUB_CLIENT_ID`/
  `GITHUB_CLIENT_SECRET` are set): `GET /auth/github/authorize` redirects
  straight to GitHub's consent screen (a real redirect, not JSON - see
  [Bugs found while building this](#bugs-found-while-building-this) for
  why that matters with a separately-hosted frontend). GitHub redirects
  back to `/auth/github/callback`, which redirects again - into the
  frontend, with the bearer token in the URL fragment (`RedirectTransport`
  in `auth.py`). An engineer who already has a password account and signs
  in with GitHub using the *same email* gets linked to that one account
  instead of creating a duplicate.

Verified end-to-end against the live Railway deployment with a real GitHub
account, not just unit tests: `/auth/github/authorize` → GitHub's consent
screen → redirected back to `/auth/github/callback` → a real user created
and a bearer token returned → that token authenticated against `/users/me`.

## Architecture

```
CSV/Excel upload
      │
      ▼
tidycsv (schema-driven cleaning, validation)
      │
      ▼
PostgreSQL (ingestion_runs, client_records, webhook_jobs, webhook_deliveries)
      │
      ▼
webhook_jobs queue ──▶ background worker (scripts/webhook_worker.py,
                        its own long-lived process) ──▶ outbound webhook
                        (retried with backoff on failure, one attempt at
                        a time, off the request path - a failed delivery
                        never fails the ingest, it's retried and logged,
                        and the data is already safely persisted either
                        way)
```

Automatic post-ingest notification is queued, not sent inline: `ingest_file()`
enqueues one `webhook_jobs` row per new record, and `scripts/webhook_worker.py`
(a separate, continuously-running process - see [Deployment](#deployment))
claims and delivers them. A manual replay (`POST /records/{id}/webhooks/replay`)
is the one exception - it still delivers synchronously in the request, since
that's a human asking for an immediate resend, not queued background work.

`config.py` holds every environment-dependent value (database URL, webhook
URL/secret, schema path) - nothing is hardcoded, so the same image runs
locally, in CI, and in production with different environment variables.

**Ingestion runs**: every upload persists an `IngestionRun` row - what
`IngestResult` reports in the moment (`rows_total`/`rows_clean`/
`rows_flagged`/`rows_dropped_duplicates`/`rows_skipped_existing`), plus
*when* it happened and *which file* it was, queryable later via
`GET /ingestion-runs`/`GET /ingestion-runs/{id}` instead of only existing
in an HTTP response that's long gone the moment nobody was looking at it.
Every `ClientRecord` links back to the run that created it
(`ingestion_run_id`), so `GET /records?ingestion_run_id=...` drills from
"this run had 3 flagged rows" straight to exactly those rows - the
frontend's Upload History page (`/uploads`) does exactly this via a "View
records" link per run. Every row is accounted for exactly once:
`rows_total == rows_clean + rows_flagged + rows_dropped_duplicates +
rows_skipped_existing` always holds (a real invariant, asserted in
`tests/test_ingestion_runs.py`) - `rows_skipped_existing` in particular
was a genuine gap found while building this: re-uploading a file that's
already fully ingested is a no-op (see below), but those skipped rows
went completely unaccounted for in any counter until this field was
added, which meant the four *other* counters silently stopped summing to
`rows_total` on a re-upload.

Each webhook delivery is signed: `X-Databridge-Signature-256` is an
HMAC-SHA256 of the exact request body, keyed with `WEBHOOK_SECRET` - the
same pattern Stripe and GitHub use, so a receiver can verify both that the
request actually came from databridge and that the body wasn't altered in
transit, without the secret itself ever going out on the wire. The same
signed body is replayed on every retry rather than re-signed per attempt,
so a receiver verifying the signature sees an identical payload whether
delivery succeeded on the first try or the third.
`examples/webhook_receiver.py` is a runnable reference receiver showing
the verification side of that.

**Retries**: a failed delivery (a non-2xx response, a timeout, a
connection error) is retried with exponential backoff -
`WEBHOOK_MAX_ATTEMPTS` tries total (default 3), `WEBHOOK_RETRY_BACKOFF_SECONDS`
as the base delay before the first retry, doubling each time after (1s,
2s, ... by default). Every attempt gets its own `WebhookDelivery` row
(`GET /records/{id}/webhooks` returns the full history, in order, not
just the latest attempt), so the audit trail shows exactly what was tried
and when, not just the final outcome. This previously was a single
best-effort attempt - a receiver's brief outage (a deploy, a cold start,
a transient 5xx) meant the notification was simply lost. See [What it
doesn't do (yet)](#what-it-doesnt-do-yet) for the real tradeoff this
still carries: retries run synchronously inside the same upload request,
not as a background job.

**Manual replay**: `POST /records/{id}/webhooks/replay` re-sends the
notification for one record on demand - a real, separate action from the
automatic retries above, not another one of them. This is what actually
gets used mid-incident: a client fixes their endpoint and asks for the
last few notifications to be resent, rather than waiting on a retry
schedule that already exhausted itself. Goes through the exact same
signing/retry path as a normal delivery. It replays the record's
*current* payload, not a stored historical one - nothing in this API
mutates a `ClientRecord` after ingest (only deletes it outright), so in
practice "current" and "at first delivery" are always the same data.

## Local development

Requires a running PostgreSQL instance.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env  # edit DATABASE_URL if needed

alembic upgrade head  # creates/updates the schema - see "Database migrations" below

uvicorn databridge.main:app --reload --app-dir src
```

```bash
curl -X POST http://127.0.0.1:8000/records/upload \
  -F "file=@examples/messy_clients.csv"
```

### Tests

Tests run against a **real** PostgreSQL database (`databridge_test`), not a
mock — the whole point of this project is proving the ingest → Postgres →
API path actually works. The schema is bootstrapped by running the real
Alembic migration chain once per test session (`alembic upgrade head`),
not `Base.metadata.create_all()` — `create_all()` only ever creates
*missing* tables, so it can't catch a migration that's wrong or
misordered relative to what's already there. That gap caused two real
bugs earlier in this project (see [Bugs found while building
this](#bugs-found-while-building-this)); running the actual migrations in
CI (a fresh Postgres container every run) closes it.

```bash
pytest
ruff check .
```

If `databridge_test` predates this (tables from an old `create_all()` run,
no `alembic_version` tracking), drop and recreate it once — the same fix
used when this project itself adopted Alembic.

## Database migrations

Schema changes go through [Alembic](https://alembic.sqlalchemy.org/), not
`Base.metadata.create_all()` - that only ever creates missing tables, it
never alters a table that already exists (found the hard way: adding a
column to an already-deployed table silently did nothing until Alembic
was introduced).

```bash
alembic upgrade head                              # apply pending migrations
alembic revision --autogenerate -m "short message" # generate a new one after changing models.py/auth_models.py
```

The Docker image runs `alembic upgrade head` before starting the server
(see the `CMD` in the Dockerfile), so a deploy always migrates first.

## Docker

```bash
docker build -t databridge .
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db" \
  -e WEBHOOK_URL="https://example.com/hook" \
  databridge
```

Note: the image needs `git` (installed in the Dockerfile) because `tidycsv`
is pulled from its GitHub repo, not from PyPI.

## Frontend

A React + TypeScript SPA in `frontend/` (Vite + Tailwind) - the whole API
surface: email+password and GitHub OAuth login, forgot/reset password,
registration (first/last name required, phone optional with a country-code
picker), upload, a searchable and sortable records list with a stats
panel, a record detail view with its webhook delivery history, delete, and
an account settings page (profile fields + change password).

**GitHub OAuth signups must complete their profile before anything else
is usable.** That flow bypasses `/auth/register` entirely (fastapi-users
creates the user directly), so a GitHub signup only ever has an email -
`ProtectedRoute` redirects to `/complete-profile` for every guarded route
until `first_name` is set (`PATCH /users/me`).

**Forgot password** (`/forgot-password` → email → `/reset-password?token=...`)
sends a real email via [Resend](https://resend.com) (`RESEND_API_KEY`,
optional - unset locally logs the link instead of sending it). **Real
delivery currently only reaches the Resend account's own email address** -
without a verified custom domain, Resend's shared `onboarding@resend.dev`
sender 403s (silently, from this app's side - `_send_email` in `auth.py`
logs and swallows it, the same as any other delivery failure) on any
other recipient. Confirmed against real production sends, not assumed -
see [`resend.com`'s own writeup](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain).
Verifying a domain on Resend is the fix; until then, every other piece of
this flow (token generation, expiry, single-use, the whole gate this
email exists to protect) is real and independently verified, only actual
inbox delivery to anyone but the account owner is not. A GitHub-OAuth-only
account (never had a real, user-chosen password) is
refused a reset token rather than letting an unauthenticated email link
bootstrap password auth onto it - the page tells the visitor to continue
with GitHub instead, and points at Settings for adding a password once
signed in. See `has_password` in `auth_models.py` and
`UserManager.forgot_password()` in `auth.py`.

Colors: emerald (brand/primary) + stone (neutral), both straight from
Tailwind's own palette - not arbitrary hex - applied as CSS variables so
every page and the favicon share one source of truth. A manual light/dark
toggle (persisted to `localStorage`, with a blocking script in
`index.html` to avoid a flash of the wrong theme on load) replaced relying
on `prefers-color-scheme` alone.

```bash
cd frontend
npm install
cp .env.example .env.local   # set VITE_API_URL to this API's URL
npm run dev
```

```bash
npm run lint    # eslint
npm run test    # vitest - unit tests for lib/ (password rules, phone
                # parsing, the greeting fallback) and a real rendered-DOM
                # test for ConfirmDialog (React Testing Library)
npm run build   # tsc && vite build
```

All three now run in CI too, alongside the backend's pytest job - the
frontend previously had no automated coverage or CI check of its own at
all (build/lint were things a developer had to remember to run locally).
`npm run test` covers pure logic (`src/lib/`) and one representative
component (`ConfirmDialog`, chosen because it's small, has real branching
behavior - backdrop vs. dialog-body clicks - and every other page depends
on it for destructive actions); the bigger data-fetching pages
(`RecordsPage`, `RecordDetailPage`) aren't covered yet - see [What it
doesn't do (yet)](#what-it-doesnt-do-yet).

Two things on the API side exist specifically to support this - both
covered above and in [Bugs found while building
this](#bugs-found-while-building-this): CORS (`FRONTEND_URL`), and GitHub
OAuth's `/authorize` being a real redirect rather than JSON, so the CSRF
cookie it sets isn't a cross-origin (and therefore browser-blocked)
third-party cookie.

Deployed separately from the API - Vercel, not Railway, since it's a
static SPA rather than a long-running process. `VITE_API_URL` is set in
Vercel's project settings for production; the API's `FRONTEND_URL` env
var must point back at that same deployed URL for CORS and the OAuth
redirect to work.

## Privacy & Terms

Real pages, not placeholders - `/privacy` and `/terms` on the deployed
frontend, linked from the footer on every page and from a required
consent checkbox at both signup paths (email+password registration and
GitHub OAuth's complete-profile step). Written from what this specific
codebase actually does (every data category, third party, and retention
claim traces back to a real field or endpoint), not adapted from a
generic template - see `frontend/src/pages/PrivacyPage.tsx` and
`TermsPage.tsx`. Both pages carry their own disclaimer: good-faith and
technically accurate, not a substitute for independent legal review.

Data retention is documented exactly as the code behaves: client data
(what you upload about your own clients) is kept for up to a year, then
deleted automatically - see [Data retention](#data-retention) below for
the real, scheduled job that enforces it, not just a policy statement.
An individual client record can also be deleted any time before that
(`DELETE /records/{id}`). Your own account (email, name, phone,
password) is kept until you delete it yourself (`DELETE /users/me`
above) - the year-long window applies only to client data, never to
your account.

## Deployment

Deployed on [Railway](https://railway.app) — a Postgres instance and this
service in the same project. Environment variables (`DATABASE_URL`,
`WEBHOOK_URL`, `WEBHOOK_SECRET`, `JWT_SECRET`, `GITHUB_CLIENT_ID`,
`GITHUB_CLIENT_SECRET`, `RESEND_API_KEY`, `EMAIL_FROM`) are set in
Railway's dashboard, never committed.

Uvicorn runs with `--proxy-headers --forwarded-allow-ips='*'` (see the
Dockerfile) - without it, every request looks like plain `http://` to the
app behind Railway's TLS-terminating proxy, which broke GitHub OAuth's
callback URL matching (see [Bugs found while building this](#bugs-found-while-building-this)).

On this project, Railway's own `${{Postgres.DATABASE_URL}}` service
reference consistently resolved to an empty string at runtime (confirmed via
`sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL`), no matter
how it was entered (typed, picked from the reference dropdown, or via the
Raw Editor) - tried and ruled out as the cause before working around it.
Building the URL from Postgres's individual `PGUSER`/`PGPASSWORD`/`PGHOST`/
`PGPORT`/`PGDATABASE` variables instead resolved correctly:
`postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`

## Backups

Client data lives in Postgres, so a mistake or corruption there shouldn't be
unrecoverable. `scripts/backup_db.py` (logic in `src/databridge/backup.py`)
runs `pg_dump -Fc` against the database and writes the dump to
`BACKUP_DIR`, then deletes dumps older than `BACKUP_RETENTION_DAYS` (defaults:
`/data/backups`, 30 days).

On Railway this runs as its **own service** in the same project - same repo/
image as `databridge`, but with:
- **Custom Start Command:** `python scripts/backup_db.py`
- **A Volume** mounted at `/data/backups` - not the app's own container
  filesystem, which is wiped on every deploy
- **A Cron Schedule** (Settings > Deploy), daily
- The same `DATABASE_URL` reference as the main service

**No credit card required.** Cloudflare R2 (and similar off-platform object
storage) was the first approach tried, but it requires a card on file, which
this project deliberately avoids - see [What it doesn't do
(yet)](#what-it-doesnt-do-yet) for the tradeoff that follows from that choice.

**Explicit tradeoff:** this protects against a bad migration, an accidental
`DROP TABLE`, or similar damage inside the database itself - not against
losing the whole Railway project or account, since the dumps live on a
Volume in that same project. A real off-platform backup (S3-compatible
storage, say) would be the stronger answer, deliberately not done here
because it required a cloud account the user didn't want to create.

Verified for real, not just "should work": built the actual Docker image,
confirmed `pg_dump --version` reports 18.6 (matching Railway's server,
confirmed via `SHOW server_version` in Railway's Console - `postgresql-
client`'s Debian-stock version is only 15), then ran the real entrypoint
inside a container against a real throwaway Postgres and confirmed the
resulting dump parses with `pg_restore --list`. Then verified the actual
deployed service too, not just the local reproduction: triggered the real
Railway Cron Schedule service on demand ("Run now") and read its deploy
logs, confirming it dumped the real production database to a real file on
the real mounted Volume (`Dumping database to
/data/backups/databridge-backup-<timestamp>.dump...` / `Dump complete`).

## Data retention

Client data - what an engineer uploads about their own clients, not the
engineer's own account - is kept for `CLIENT_DATA_RETENTION_DAYS`
(default 365) after it's ingested, then deleted automatically.
`scripts/retention_sweep.py` (logic in `src/databridge/retention.py`)
deletes every `IngestionRun` older than the window in one statement - the
`ON DELETE CASCADE` on `ClientRecord`/`WebhookDelivery`'s foreign keys
(added by migration `e986a7123298`, for `DELETE /users/me`'s cascading
account erasure - see the API table above) takes its client records and
their webhook delivery history with it in the same database operation,
not a separate per-table pass. Also deletes
any `ClientRecord` past the window with no `ingestion_run_id` at all
(predates that column, so there's no run to cascade from) directly, so a
record's age decides its fate regardless of when the run-linking feature
shipped relative to it.

**Deliberately scoped to client data only** - an engineer's own account
(email, name, phone, password) is never touched by this job, no matter
how old or inactive; only `DELETE /users/me` removes that, on the
account holder's own request. The [Privacy Policy](#privacy--terms)
reflects exactly this: client data retained up to a year, account data
retained until you delete it yourself.

Same pattern as Backups above - a scheduled Railway service, same repo/
image, `python scripts/retention_sweep.py` as its Custom Start Command,
a daily Cron Schedule, the same `DATABASE_URL` reference. No Volume
needed (nothing is written to disk, only deleted from the database).

Verified against a real Postgres test database, not mocks
(`tests/test_retention.py`): a run backdated past the window is deleted
and its cascade actually removes the client records and webhook
deliveries it owned (checked directly via the database, not just the
function's return value); a recent run is left alone; an orphaned
record with no `ingestion_run_id` is caught by the direct pass; the
owning user account survives untouched; a custom retention window
(monkeypatched shorter than the 365-day default) is respected. Setting
up the actual Railway Cron Schedule service for this (mirroring
Backups' setup above) is a deployment step, not something exercised by
the test suite.

## Bugs found while building this

Found and fixed rather than worked around silently - one in `tidycsv`
itself (reusing it as a real dependency surfaced it), the rest in this
project's own code or in actually deploying it:

1. **`None` silently became the string `"NaN"`** in JSON responses. On
   pandas 3.x, assigning a plain Python list containing `None` into a
   DataFrame column upcasts `None` to the float `NaN`, because pandas'
   new default `str` column dtype doesn't preserve `None` the way the old
   `object` dtype did. `tidycsv`'s own tests never caught this because they
   only inspect `.to_csv()` output, where `None` and `NaN` both render as
   an empty cell. Fixed upstream in `tidycsv` (assign via
   `pd.Series(..., dtype=object)`), **and** a second occurrence of the same
   root cause in this project's own `ingest.py` (`.iterrows()` rebuilds
   each row as a fresh Series and re-triggers the same coercion - fixed by
   reading columns via `.at[]` instead).
2. **On Railway, `DATABASE_URL` was never a real Postgres connection
   string.** First it was the local-dev default (`postgres:postgres@127.0.0.1`)
   entered manually into the dashboard instead of a service reference -
   fixed by pointing it at the Postgres service. Then Railway's own
   `${{Postgres.DATABASE_URL}}` reference resolved to an empty string at
   runtime regardless of how it was entered - fixed by building the
   connection string from Postgres's individual `PGUSER`/`PGHOST`/etc.
   variables instead (see [Deployment](#deployment)). Found by reading the
   actual container crash logs each time rather than assuming the dashboard
   configuration was correct because it looked right.
3. **The service crashed in Docker but not locally.** The schema file path
   was computed relative to `__file__`'s location on disk, which works
   under an editable install (`pip install -e .`, where the source stays in
   place) but breaks the moment the package is installed normally - as it
   is in the Docker image - because the installed package ends up in
   `site-packages`, nowhere near `examples/`. Fixed by making the schema
   path a configuration value (`schema_path`, defaulting to a path resolved
   relative to the process's working directory) instead of a `__file__`
   computation - found by actually running the built image, not by
   assuming it would work because it worked with `uvicorn --reload`.
4. **GitHub OAuth's callback URL never matched, on the first real login
   attempt it would have been tried.** Railway terminates TLS at its edge
   and forwards plain HTTP to the container, with the real scheme carried
   in `X-Forwarded-Proto` - a header uvicorn ignores unless told to trust
   it. Every request looked like `http://...` to the app, so the OAuth
   library generated an `http://` `redirect_uri` that didn't match the
   `https://` URL registered on GitHub's side. Fixed with
   `--proxy-headers --forwarded-allow-ips='*'` on uvicorn's start command -
   verified by building the real Docker image and comparing the generated
   `redirect_uri` with and without a simulated `X-Forwarded-Proto: https`
   header before trusting it was fixed.
5. **Adopting Alembic mid-project crash-looped Railway** with
   `psycopg.errors.DuplicateTable: relation "users" already exists`. An
   earlier deploy (before Alembic replaced `create_all()`) had already
   built the exact same schema live, from the same models - so the
   baseline migration's `CREATE TABLE users` collided with a table that
   already existed. Since the live schema and what the migration would
   create were identical, the fix was `alembic stamp head` (mark the
   migration applied without re-running its DDL), done as a one-off
   Dockerfile change for a single deploy and reverted immediately after.
6. **`POST /records/upload` blocked the whole process on every request,
   not just the uploader's.** It's declared `async def` (needed for
   `await file.read()`), but called `ingest_file()` - CSV parsing, several
   synchronous DB round-trips, and a blocking `httpx.post` to the webhook
   receiver with up to a 5s timeout - directly. FastAPI only auto-offloads
   *sync* `def` routes to a worker thread; a sync call made directly
   inside an async route runs on the single event loop thread instead -
   and with this project's single uvicorn worker, that thread **is** the
   whole process. Found during a deliberate scalability/reliability/
   availability/performance review, not a user report. Fixed with
   `run_in_threadpool` (the same mechanism FastAPI itself uses for sync
   routes). Verified deterministically, not by racing timers: a test
   checks which real OS thread actually executes `ingest_file`, confirmed
   to fail against the pre-fix code and pass with the fix restored, before
   trusting it (see `tests/test_concurrency.py`).
7. **GitHub login 400'd with `OAUTH_INVALID_STATE` on every attempt, once
   there was a real frontend on a different origin than the API.**
   `GET /auth/github/authorize` (fastapi-users' own) returns JSON and sets
   a CSRF cookie on that same response - meant to be `fetch()`'d by a SPA,
   which then navigates the browser to the JSON body's `authorization_url`
   itself. With the frontend on a different origin, that fetch is
   cross-origin, and browsers that block third-party cookies by default
   (Chrome included) silently drop the cookie it tried to set -
   `credentials: "include"` on the fetch didn't change that. Traced by
   comparing what a direct `curl` to the endpoint returned (a valid
   `Set-Cookie`) against what the browser's DevTools actually stored
   (nothing), not by guessing. Fixed by replacing the route with one that
   redirects straight to GitHub instead of returning JSON - reusing
   fastapi-users' own CSRF/state-generation functions rather than
   reimplementing them - so the browser's own top-level navigation to the
   API's domain is what sets the cookie, first-party.
8. **`logger.info(...)` calls were silently discarded, everywhere in the
   app, in both local dev and production.** Nothing in the process
   configures logging - Python's root logger defaults to `WARNING` with
   zero handlers attached, so an INFO record gets dropped at the
   effective-level check before it ever reaches output; uvicorn's own
   `dictConfig` only wires up its own `uvicorn`/`uvicorn.access` loggers,
   never root or this app's. Found while adding the forgot-password flow
   below: the dev-fallback log line (no `RESEND_API_KEY` configured) never
   appeared anywhere, in a real terminal, not a test. Fixed by giving the
   `databridge` logger namespace its own explicit level and handler in
   `main.py`.

## What it doesn't do (yet)

- Single schema for the whole service - a real multi-tenant version would
  need a schema per client, not one shared `examples/schema.yaml`.
- **Webhook retries run synchronously, inside the same upload request**
  (see [Architecture](#architecture)) - not as a separate background job,
  since there's no queue/broker in this project. A receiver that's fully
  down adds real, visible latency to that one upload's response (up to
  ~3s with the default backoff schedule) instead of the retries happening
  invisibly after the response has already gone back. A real
  multi-tenant version would move this to a background worker with
  durable retry state, so a process restart mid-retry can't lose an
  in-flight attempt the way it currently could.
- No email verification - fastapi-users supports it, but this project
  deliberately doesn't enforce it: real Resend delivery only reaches the
  Resend account's own address without a verified custom domain (see
  [Frontend](#frontend)'s forgot-password caveat), and requiring
  verification with delivery that broken would permanently lock out
  every real registrant but the account owner. Considered and reverted
  after confirming the delivery limitation against real production
  sends - worth revisiting once a domain is verified. Password-reset
  **is** implemented despite the same delivery caveat, since a failed
  reset only leaves someone unable to self-serve a new password, it
  never locks them out of an account they already had access to. A
  GitHub-OAuth-only account is deliberately refused a reset token - see
  `UserManager.forgot_password()` in `auth.py` - since there's no real
  password on that account to reset, only Settings (while signed in) can
  add one.
- No roles beyond "engineer" - every authenticated user has the same
  permissions on their own records; there's no admin/read-only distinction.
- **Single uvicorn worker, single Railway instance, single Postgres
  instance** - no horizontal scaling, no redundancy. An outage of that one
  container or that one database is full downtime; there's no failover.
  Explicit tradeoff for a single-tenant portfolio project, not something
  hidden - see [Backups](#backups) for the one piece of disaster recovery
  that *does* exist (protects against DB-internal mistakes, not against
  losing the instance or the account).
- **Rate limiting state is in-memory** (`slowapi`'s default) - correct for
  the single instance above, but wouldn't be if a second instance were ever
  added without also moving the limiter to shared storage (e.g. Redis);
  each instance would then enforce its own separate 5/minute instead of
  one shared limit.
- **Frontend test coverage is partial.** `npm run test` covers pure logic
  (`src/lib/`) and one representative component (`ConfirmDialog`) - the
  pages that actually fetch and render data (`RecordsPage`,
  `RecordDetailPage`, `IngestionRunsPage`, the auth forms) have no
  automated tests yet, only manual browser verification against a real
  local backend. Better than the zero frontend coverage (and no frontend
  CI at all) this project had before, not yet equivalent to the
  backend's 73 pytest tests against a real database.

## Security

Found via a deliberate review, not a user report:

- **`JWT_SECRET` was never actually set in Railway** - every login token
  was signed with the obviously-fake default from `config.py`
  (`insecure-local-dev-secret-do-not-use-in-production`), sitting right
  there in the source. Confirmed exploitable, not just theoretical: forged
  a JWT for a real user with that known default and it authenticated
  successfully against the live `/users/me`. Fixed by generating a real
  random secret and setting it in Railway - re-verified afterwards that
  the forged token now gets 401 and a fresh real login still works.
- **No cap on `/records/upload`** - an oversized file was read entirely
  into memory before tidycsv/pandas ever saw it. Fixed: reads in bounded
  1 MB chunks and rejects (413) as soon as `max_upload_size_mb` (default
  10) is crossed, rather than trusting the client-controlled
  `Content-Length` header.
- **No rate limiting** on `/auth/jwt/login` or `/auth/register` -
  unthrottled brute-force and credential-stuffing. Fixed with
  [slowapi](https://github.com/laurentS/slowapi): 5/minute on those two
  endpoints specifically, 60/minute as a general default across the rest
  of the API. Keyed on the caller's real IP via `X-Forwarded-For` (see
  `--proxy-headers` above) - verified with two different forwarded IPs
  against the real Docker image that one IP hitting the limit doesn't
  throttle another, which it would if the key still resolved to
  Railway's own proxy IP for everyone.
- **No password strength requirement** - a one-character password was
  accepted at registration. Fixed: `UserManager.validate_password`
  (`auth.py`) now requires at least 8 characters, one uppercase letter,
  one lowercase letter, one digit, and one special character - the
  documented fastapi-users extension point for this, not a bespoke
  validator bolted on elsewhere. Verified with real registration calls
  for each individual missing requirement (too short, no uppercase, no
  lowercase, no digit, no special character) and one that satisfies all
  of them.
- Also checked and ruled out as **not** a hole: `PATCH /users/me` cannot
  be used to self-promote to `is_superuser` - fastapi-users' safe-update
  default already strips that field, confirmed by actually trying it.

- **Known, currently unpatched: `react-router-dom` 6.30.6 (the latest
  6.x release - there is no patched 6.x) carries a moderate-severity
  open-redirect advisory** (`GHSA-wrjc-x8rr-h8h6` - a backslash-prefixed
  value passed to `<Link>`/`useNavigate` can be parsed as
  protocol-relative and redirect off-site). Checked this app's actual
  exposure rather than assuming the CVE applies as shipped: every
  `navigate()`/`<Link to=` call in the frontend uses a hardcoded literal
  path or a template built from this app's own IDs (`/records/${id}`,
  `/?ingestion_run_id=${run.id}`) - nowhere does user- or
  attacker-controlled input reach a navigation target directly, so the
  specific exploit vector isn't reachable through this app's own code as
  written. The dependency itself is still vulnerable, though, and the
  real fix (upgrading to React Router v7, a breaking major version) is a
  deliberate migration worth doing on its own, not a drive-by dependency
  bump - not done in this pass.

## License

MIT
