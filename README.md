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
| `POST` | `/records/upload` | Upload a CSV/Excel file, clean + persist it (tagged to the caller), fire webhooks for new records |
| `GET` | `/records` | List **your own** records, optionally `?has_issues=true/false` |
| `GET` | `/records/{id}` | Fetch one of **your own** records - 404 (not 403) if it belongs to someone else, or doesn't exist |
| `GET` | `/records/{id}/webhooks` | Audit log of webhook delivery attempts for one of your own records |
| `DELETE` | `/records/{id}` | Permanently erase one of your own records (and its webhook delivery history) - the GDPR right-to-erasure endpoint |

Re-uploading a file already ingested (matched by email, scoped to the
uploading engineer) is a no-op, not a duplicate insert or an error - two
different engineers uploading a client with the same email are two
separate records, not duplicates of each other.

## Authentication

Built on [fastapi-users](https://fastapi-users.github.io/fastapi-users/)
rather than hand-rolled password hashing/JWT/OAuth - real production
systems don't reinvent this. Two ways in, both landing on the same kind of
account:

- **Email + password**: `POST /auth/register`, then `POST /auth/jwt/login`
  (form-encoded `username`/`password`) for a bearer token
- **GitHub OAuth** (optional - only enabled when `GITHUB_CLIENT_ID`/
  `GITHUB_CLIENT_SECRET` are set): `GET /auth/github/authorize` starts the
  flow. An engineer who already has a password account and signs in with
  GitHub using the *same email* gets linked to that one account instead of
  creating a duplicate.

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
PostgreSQL (client_records, webhook_deliveries)
      │
      ▼
Outbound webhook (best-effort - a failed delivery never fails the ingest,
                   it's logged and the data is already safely persisted)
```

`config.py` holds every environment-dependent value (database URL, webhook
URL/secret, schema path) - nothing is hardcoded, so the same image runs
locally, in CI, and in production with different environment variables.

Each webhook delivery is signed: `X-Databridge-Signature-256` is an
HMAC-SHA256 of the exact request body, keyed with `WEBHOOK_SECRET` - the
same pattern Stripe and GitHub use, so a receiver can verify both that the
request actually came from databridge and that the body wasn't altered in
transit, without the secret itself ever going out on the wire.
`examples/webhook_receiver.py` is a runnable reference receiver showing
the verification side of that.

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
API path actually works.

```bash
pytest
ruff check .
```

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

## Deployment

Deployed on [Railway](https://railway.app) — a Postgres instance and this
service in the same project. Environment variables (`DATABASE_URL`,
`WEBHOOK_URL`, `WEBHOOK_SECRET`, `JWT_SECRET`, `GITHUB_CLIENT_ID`,
`GITHUB_CLIENT_SECRET`) are set in Railway's dashboard, never committed.

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

## What it doesn't do (yet)

- Single schema for the whole service - a real multi-tenant version would
  need a schema per client, not one shared `examples/schema.yaml`.
- No webhook retry logic - a failed delivery is logged, not automatically
  retried.
- No email verification or password-reset flow - fastapi-users supports
  both, but this project doesn't send the emails yet, so an engineer who
  loses their password currently has no self-service way back in.
- No roles beyond "engineer" - every authenticated user has the same
  permissions on their own records; there's no admin/read-only distinction.

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

## License

MIT
