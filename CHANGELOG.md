# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
nothing has been tagged as a release yet, so everything below is under
`[Unreleased]`.

## [Unreleased]

### Added
- Frontend: real brand colors (emerald/stone, from Tailwind's own
  palette) replacing the placeholder blue/slate scheme, applied as CSS
  variables so the favicon and every page share one source of truth,
  plus a manual light/dark toggle (localStorage-persisted, with a
  blocking script to avoid a theme-flash on load) instead of relying
  on `prefers-color-scheme` alone.
- Frontend: registration's phone field split into a country-code picker
  (ITU calling codes) + number, joined into one string before being
  sent to the API - no backend schema change needed.
- Frontend: GitHub OAuth signups (email only - that flow bypasses
  `/auth/register`'s required first_name/last_name entirely) are now
  forced through a `/complete-profile` step before anything else in
  the app is reachable, via `ProtectedRoute`.

### Fixed
- **The country-code `<select>`'s option list was barely readable in
  dark mode** - `color-scheme: dark` alone wasn't enough to make the
  native dropdown popup follow the app's theme. Fixed with an explicit
  `background-color`/`color` on each `<option>`, not just the closed
  `<select>`. Found by the user testing the actual dropdown, not by
  anything a screenshot of the closed control would have caught.
- **`POST /records/upload` blocked the whole process on every request, not
  just the uploader's.** It's `async def` (needed for `await file.read()`),
  but called `ingest_file()` - CSV parsing, several synchronous DB
  round-trips, and a blocking `httpx.post` to the webhook receiver with up
  to a 5s timeout - directly instead of offloading it. FastAPI only
  auto-offloads *sync* `def` routes to a worker thread; with this
  project's single uvicorn worker, a sync call left running directly on
  the event loop thread blocks the entire process, not just that request.
  Found during a deliberate scalability/reliability/availability/
  performance review. Fixed with `run_in_threadpool`. Verified
  deterministically (which real OS thread executes `ingest_file`, not a
  timing race) - confirmed the new test fails against the pre-fix code
  and passes with it restored, before trusting it.
- **Tests bootstrapped their schema with `Base.metadata.create_all()`,
  not real Alembic migrations** - `create_all()` only ever creates
  *missing* tables, so a test suite built on it structurally cannot catch
  a migration that's wrong or misordered against what's already there.
  That exact gap caused two real production bugs earlier in this project
  (the `NoReferencedTableError`/`DuplicateColumn` history further down
  this changelog) - neither would have failed CI. Switched to running
  the real migration chain once per test session (`alembic upgrade
  head`); CI's Postgres service container starts empty every run, so
  this now exercises the full chain there, automatically, every run.
  Prompted by hitting the same stale-schema problem a third time
  locally (while adding the profile fields below) and the user asking
  whether `create_all()` should even be able to alter existing tables.

### Added
- Frontend (`frontend/`): a React + TypeScript SPA - login (email+password
  and GitHub OAuth), registration, upload, a filterable records list,
  a record detail view with its webhook delivery history, and delete.
  Backend changes needed to support it:
  - CORS: only `settings.frontend_url` may call the API from a browser.
  - A custom fastapi-users `Transport` (`RedirectTransport`) so a
    successful GitHub login redirects back into the SPA with the JWT in
    the URL fragment, instead of returning a bare JSON body on the
    API's own origin - built on fastapi-users' own extension point (the
    same one `BearerTransport`/`CookieTransport` implement).
  - GitHub's own `/auth/github/authorize` had to stop being a JSON
    endpoint the SPA `fetch()`-ed and become a real redirect itself.
    Found the hard way: with the SPA on a different origin than the
    API, the CSRF cookie that route sets was being set via a
    *cross-origin* fetch - which browsers that block third-party
    cookies by default (Chrome included) silently drop, `credentials:
    "include"` on the fetch notwithstanding. Every attempt 400'd with
    `OAUTH_INVALID_STATE` until traced to this. Fixed by replacing the
    route with one that redirects straight to GitHub (reusing
    fastapi-users' own CSRF/state-generation functions, not
    reimplementing them) - the browser's own top-level navigation to
    the API's domain is what sets the cookie, first-party, same as the
    callback navigation right after it. Verified for real: a real
    (throwaway) FastAPI app + a real `GitHubOAuth2` client asserting
    the redirect target, the cookie, and that the cookie's value
    matches what's embedded in the state param - and, after deploying,
    the full flow end to end in a real browser against the real
    deployed API: GitHub's consent screen, redirected back into the
    running frontend already signed in.
- Engineer profile: `first_name`/`last_name` (required at registration)
  and `phone` (optional) on `User`. Nullable at the DB level regardless
  - existing users, and every GitHub OAuth signup (which bypasses
  `UserCreate` entirely), have `NULL` here. Drives the frontend's
  greeting, which falls back to the email when unset.
- Postgres backups: `scripts/backup_db.py` (logic in
  `src/databridge/backup.py`) runs `pg_dump -Fc` on a schedule and writes
  dumps to `BACKUP_DIR`, deleting dumps older than
  `BACKUP_RETENTION_DAYS`. Runs as its own Railway service (Cron Schedule,
  Custom Start Command, its own Volume mounted at `/data/backups`) rather
  than inside the main app's container, which is wiped on every deploy.
  Originally built against Cloudflare R2, reworked to a local/Volume path
  instead because the user won't provide a credit card to Cloudflare (or
  any similar service) - no cloud account required this way. Explicit
  tradeoff, documented in README's Backups section: protects against a
  mistake or corruption inside the database itself, not against losing
  the whole Railway project/account. The Docker image now installs
  `postgresql-client-18` via the PGDG apt repo (the stock Debian package
  is v15) to match Railway's actual Postgres version (18.6, confirmed via
  `SHOW server_version` in Railway's Console) - verified for real: built
  the image, confirmed `pg_dump --version` reports 18.6, then ran the
  real entrypoint against a real throwaway Postgres inside a container
  and confirmed the dump parses with `pg_restore --list`.

### Added
- FastAPI service: `POST /records/upload`, `GET /records`,
  `GET /records/{id}`, `GET /records/{id}/webhooks`, `GET /health`
- PostgreSQL persistence (SQLAlchemy) with two tables: `client_records` and
  `webhook_deliveries` (a full audit log of every notification attempt,
  success or failure)
- Reuses [tidycsv](https://github.com/VictorPaniello/tidycsv) as a real
  dependency for cleaning/validating uploaded files, rather than
  reimplementing that logic
- Idempotent ingestion: re-uploading a file already ingested (matched by
  email) inserts nothing new
- Outbound webhook delivery that never fails the ingest request on a
  delivery failure - the record is already persisted by the time delivery
  is attempted
- Test suite (9 tests) running against a real PostgreSQL database, not a
  mock
- Dockerfile (verified by actually building and running the image, not
  assumed to work from the source alone) and GitHub Actions CI with a
  Postgres service container

### Added
- Per-engineer authentication and authorization: each engineer signs in
  (email+password or GitHub OAuth) and only sees their own client records.
  Built on [fastapi-users](https://fastapi-users.github.io/fastapi-users/)
  (password hashing, JWT, OAuth) rather than hand-rolled auth.
- `POST /auth/register`, `POST /auth/jwt/login`, `POST /auth/jwt/logout`,
  `GET /auth/github/authorize` + `/auth/github/callback` (only registered
  when `GITHUB_CLIENT_ID`/`SECRET` are set), `GET`/`PATCH /users/me`
- `User` and `OAuthAccount` tables (`auth_models.py`); `ClientRecord` gained
  a nullable `owner_id` foreign key to `User`. `associate_by_email=True` on
  the GitHub OAuth router links a password account and a GitHub sign-in
  sharing the same email into one account instead of creating a duplicate.
- `/records/upload`, `/records`, `/records/{id}`, `/records/{id}/webhooks`
  now all require a bearer token and are scoped to the calling engineer's
  own `owner_id` - a record belonging to someone else 404s rather than
  403s, so its existence isn't observable either. Covered by new tests:
  an unauthenticated upload is rejected, and two engineers uploading the
  same file each only ever see their own records.
- A second, async SQLAlchemy engine (`auth_db.py`) used only by the auth
  subsystem - fastapi-users requires an async session; the rest of the
  service stays on the existing synchronous engine rather than a full
  async rewrite.
- [Alembic](https://alembic.sqlalchemy.org/) migrations, replacing
  `Base.metadata.create_all()` for schema management (see "Fixed" below
  for why).
- GitHub OAuth verified end-to-end against the live Railway deployment
  with a real GitHub account, not just unit tests: `/authorize` → GitHub's
  consent screen → `/callback` → a real user created and a bearer token
  returned → that token authenticated against `/users/me`.

### Security
- **No way to actually delete a client's data.** From a reliability/trust
  review (this project handles real client PII - name, email, phone):
  there was no deletion endpoint at all, so honoring a GDPR
  right-to-erasure request was impossible via the API. Added
  `DELETE /records/{id}`: a real delete, not a soft-delete flag, scoped
  to the caller's own records (404, not 403, for someone else's - same
  as every other `/records/{id}*` endpoint). `WebhookDelivery.record_id`'s
  foreign key now has `ON DELETE CASCADE` (new migration
  `9d13056a9c48`), so a record's delivery audit trail is erased with it
  rather than left as orphaned rows still carrying the erased record's
  id. Verified with real tests: deleting a record makes it a genuine 404
  afterwards (not a "deleted" flag still showing up), its webhook
  deliveries are actually gone from the table (not just hidden), and
  another engineer's delete attempt on your record 404s and leaves it
  untouched.
- **Webhooks sent the raw `WEBHOOK_SECRET` as a header value
  (`X-Databridge-Secret`) on every delivery**, instead of only ever using
  it locally to prove authenticity - weaker than necessary, and gave a
  receiver no way to detect a body tampered with in transit. Replaced
  with HMAC-SHA256 signing (`X-Databridge-Signature-256: sha256=<hex>`),
  the same pattern Stripe and GitHub use: the secret never goes out on
  the wire, and the receiver can verify both sender and integrity by
  recomputing the HMAC over the raw body it received. Added
  `examples/webhook_receiver.py`, a runnable reference receiver.
  Verified for real: a local test suite against a real HTTP server (not
  a mocked transport) proves a receiver can recompute the exact
  signature and that a tampered body fails verification; separately ran
  the actual example receiver as its own process and sent it a real
  webhook end to end - correct secret verified and logged the event,
  wrong secret got a real 401, both persisted correctly to
  `webhook_deliveries`.
- **`JWT_SECRET` was never set in Railway** - production was signing every
  login token with the obviously-fake default from `config.py`
  (`insecure-local-dev-secret-do-not-use-in-production`), which is right
  there in the source. Confirmed exploitable, not just theoretical: forged
  a JWT for a real user with that known default and it authenticated
  successfully against the live `/users/me`. Fixed by generating a real
  random secret and setting it in Railway - re-verified afterwards that
  the forged token now gets 401 and a fresh real login still works. Found
  by a deliberate security review, not by a user report.
- **No cap on `/records/upload`** - an oversized file was read entirely
  into memory before tidycsv/pandas ever saw it. Fixed: reads in bounded
  1 MB chunks, rejects (413) as soon as `max_upload_size_mb` (default 10)
  is crossed, rather than trusting the client-controlled `Content-Length`
  header. Verified with a real test (tiny limit set just for that test)
  that the chunked reader actually cuts an oversized upload off.
- **No rate limiting** on `/auth/jwt/login` or `/auth/register` -
  unthrottled brute-force and credential-stuffing. Fixed with
  [slowapi](https://github.com/laurentS/slowapi): 5/minute on those two
  endpoints, 60/minute as a general default across the rest of the API.
  Rate limiting is disabled globally in the test suite (most tests
  register/log in their own engineer and would otherwise exceed 5/minute
  on their own) and re-enabled only in its own dedicated test. Verified
  the IP-keying works correctly behind Railway's proxy by building the
  real Docker image and hitting it with two different `X-Forwarded-For`
  values - one IP hitting the limit doesn't throttle the other, which it
  would if the key still resolved to Railway's own proxy IP for everyone.
- **No password strength requirement** - a one-character password was
  accepted at registration. Fixed by overriding
  `UserManager.validate_password` (`auth.py`), fastapi-users' documented
  extension point for this: requires at least 8 characters, one
  uppercase letter, one lowercase letter, one digit, and one special
  character. Verified with 6 real registration calls (one per missing
  requirement, plus one satisfying all of them), not just that the
  method exists.
- **Adding `owner_id` twice**: the baseline migration was autogenerated
  after `owner_id` already existed on the model, so it included the
  column in `client_records` from the start - which matched a fresh
  database, but not Railway's, which had this table from before
  `owner_id` existed and was `alembic stamp head`-ed onto this revision
  without that `CREATE TABLE` ever actually running. Leaving `owner_id`
  in both this migration and the later incremental one made any brand
  new database fail with `DuplicateColumn` the moment both ran in
  sequence. Found by actually building the Docker image and running
  `alembic upgrade head` against a fresh database while verifying rate
  limiting - not by assuming the two migrations composed correctly.
  Removed from the baseline; the column is now added exactly once, by
  the incremental migration, on every database.

### Fixed (deployment, continued)
- `GET /records` returned 500 (`psycopg.errors.UndefinedColumn: column
  client_records.owner_id does not exist`) against the live Railway
  database. Root cause: the earlier `alembic stamp head` fix (see below)
  assumed the live schema matched the baseline migration exactly, but
  `client_records` had been created by `create_all()` *before*
  `owner_id` was added to the model - `create_all()` never alters an
  existing table, so that column was never actually added to
  production, even though `alembic_version` claimed the schema was
  fully up to date. Fixed with a real incremental migration
  (`add_owner_id_to_client_records`) rather than another stamp. Verified
  by reproducing the exact production state locally first (apply the
  baseline migration, then manually drop `owner_id` to match what
  production actually had) and confirming the new migration - and the
  full test suite - both succeed against that reproduction before
  trusting it for the real database.

- GitHub OAuth's callback URL never matched. Railway terminates TLS at
  its own edge and forwards plain HTTP to the container with the real
  scheme in `X-Forwarded-Proto`; uvicorn ignores that header by default,
  so every request looked like `http://...` to the app - the OAuth
  library then generated a `redirect_uri` of `http://databridge-
  production-372d.up.railway.app/auth/github/callback`, which doesn't
  match the `https://` URL registered on GitHub's side. Fixed by adding
  `--proxy-headers --forwarded-allow-ips='*'` to uvicorn's start command.
  Verified by building the real Docker image and comparing the generated
  `redirect_uri` with and without a simulated `X-Forwarded-Proto: https`
  header - `http://127.0.0.1:8000/...` without it, the correct
  `https://databridge-production-372d.up.railway.app/...` with it.

- Railway crash-looped on `psycopg.errors.DuplicateTable: relation "users"
  already exists` the moment the Alembic baseline migration deployed. An
  earlier deploy (before Alembic replaced `create_all()`) had already
  built the exact same schema on Railway's live database via
  `Base.metadata.create_all()`, from the same models - so the baseline
  migration's `CREATE TABLE users` collided with a table that already
  existed. Since the live schema and what the migration would create are
  identical, the fix was `alembic stamp head` (mark the migration applied
  without re-running its DDL) rather than dropping and recreating
  anything - done as a one-off Dockerfile CMD change for a single deploy,
  reverted back to `alembic upgrade head` immediately after.

### Fixed (this project, continued)
- `Base.metadata.create_all()` only ever creates missing tables - it never
  alters one that already exists. Adding `owner_id` to the already-deployed
  `client_records` table did nothing against the local test database until
  noticed by actually re-running the test suite, not assumed to have
  worked. Replaced with Alembic: a baseline migration captures the full
  schema, and the Docker image now runs `alembic upgrade head` before
  starting the server.
- fastapi-users' `SQLAlchemyBaseOAuthAccountTableUUID` hardcodes its
  `user_id` foreign key to `ForeignKey("user.id")` (singular), matching
  the library's own examples. This project's `User` table is `"users"`
  (plural, consistent with `client_records`/`webhook_deliveries`), so the
  FK was redeclared explicitly - found via `NoReferencedTableError` when
  actually creating the tables against real Postgres.
- The autogenerated Alembic baseline migration referenced
  `fastapi_users_db_sqlalchemy.generics.GUID` without importing it -
  Alembic's autogenerate doesn't add that import automatically. Found by
  actually running `alembic upgrade head`, not by trusting the generated
  file.

### Fixed (upstream, in tidycsv)
- `None` silently coerced to the float `NaN` (surfacing as the string
  `"NaN"` in JSON responses) on pandas 3.x, due to its new default `str`
  column dtype not preserving `None` on plain-list assignment. Found while
  integrating `tidycsv` here; fixed at the source with a regression test,
  plus a second occurrence of the same root cause in this project's own
  `ingest.py` (`.iterrows()` re-triggers the same coercion - fixed by using
  `.at[]` column access instead).

### Fixed (this project)
- Schema file path was computed relative to `__file__`, which works under
  an editable install but breaks once the package is installed normally
  (as in the Docker image) since the installed package lands in
  `site-packages`, not next to `examples/`. Found by actually running the
  built Docker image rather than assuming `uvicorn --reload` behavior would
  carry over; fixed by making the schema path a configuration value.
- Railway deployment: `DATABASE_URL` defaulted to `postgres://`/`postgresql://`
  (no driver), which SQLAlchemy resolves to psycopg2 - not installed here
  (this project uses psycopg3). Added a `field_validator` on
  `Settings.database_url` that rewrites either scheme to
  `postgresql+psycopg://`. Separately, Railway's own
  `${{Postgres.DATABASE_URL}}` service reference resolved to an empty
  string at runtime on this project regardless of how it was entered;
  worked around by building the connection string from Postgres's
  individual `PGUSER`/`PGPASSWORD`/`PGHOST`/`PGPORT`/`PGDATABASE`
  variables instead. Found by reading the actual deployment crash logs,
  not by assuming the dashboard configuration was correct.
