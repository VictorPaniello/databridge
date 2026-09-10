# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
nothing has been tagged as a release yet, so everything below is under
`[Unreleased]`.

## [Unreleased]

### Added
- **Persisted ingestion runs.** Before this, the only record of what an
  upload actually did was `IngestResult` - the HTTP response, gone the
  moment it wasn't being looked at (a closed tab, a script that didn't
  log it, a client asking days later "did my 50,000-row file actually
  finish?"). A new `IngestionRun` row now persists every upload's
  summary - `rows_total`/`rows_clean`/`rows_flagged`/
  `rows_dropped_duplicates`/`rows_skipped_existing`, plus when it
  happened and which file it was - queryable via `GET /ingestion-runs`
  and `GET /ingestion-runs/{id}`. Every `ClientRecord` now links back to
  the run that created it (`ingestion_run_id`, migration `0949c3dd5b88`
  - nullable, like `owner_id`, since there's no way to reconstruct which
  run produced a record ingested before this column existed), and
  `GET /records` gained an `?ingestion_run_id=` filter so a caller can
  drill from "this run had 3 flagged rows" straight to exactly those
  rows. `IngestResult` and the upload endpoint were refactored to read
  their stats off the same `IngestionRun` row rather than recomputing
  them separately, so there's one source of truth, not two that could
  drift.

  **A real gap found while building this, not assumed away:**
  `rows_total` didn't sum to `rows_clean + rows_flagged +
  rows_dropped_duplicates` on a re-upload - rows skipped because they
  matched an already-ingested email (see "Re-uploading a file" above)
  went completely unaccounted for in any counter. Added
  `rows_skipped_existing` specifically to close that gap;
  `tests/test_ingestion_runs.py::test_every_row_is_accounted_for_exactly_once`
  now asserts the invariant holds, and
  `test_reuploading_creates_a_second_run_where_every_row_is_skipped_existing`
  is the regression test for the exact case that exposed it (re-uploading
  `messy_clients.csv` a second time: `rows_clean=0`, `rows_flagged=0`,
  and previously nothing else told you where those 5 rows went).

  Frontend: a new "Upload history" page (`/uploads`, linked from the
  header) listing every past run with its counts, each with a "View
  records" link that filters the records list down to exactly that
  run's rows (`?ingestion_run_id=` in the URL, with a "Clear filter"
  banner) - and the just-completed upload's own result banner now links
  straight into this history instead of only showing counts that vanish
  on the next page load.

  Also fixed while adding the `ingestion_runs` table: the test suite's
  `_clean_tables` fixture (`conftest.py`) truncated a fixed list of
  tables that didn't include the new one - Postgres refuses to truncate
  a table another (non-listed) table still has a live FK pointing at, so
  every DB-touching test failed with `NotSupportedError` until
  `ingestion_runs` was added to that list. A real, reproducible gotcha of
  adding any new FK-holding table to this schema, not specific to this
  feature - worth remembering the next time one gets added.
- **Manual webhook replay.** `POST /records/{id}/webhooks/replay`
  re-sends a record's notification on demand - a real, separate action
  from the automatic retries above, not another one of them. This is
  what actually gets used mid-incident: a client fixes their receiver
  and asks for the last few notifications to be resent, rather than
  waiting on a retry schedule that already exhausted itself. Goes
  through the exact same `notify_new_record()` signing/retry path a
  normal delivery does - a replay that hits a transient failure retries
  the same way an original delivery would. Replays the record's
  *current* payload; there's no stored historical payload to resend
  verbatim (`WebhookDelivery` only ever persisted each attempt's
  outcome, not its request body) - in practice this is moot, since
  nothing in this API mutates a `ClientRecord` after ingest, only
  deletes it outright, so "current" and "at first delivery" are always
  the same data. 400s if no `WEBHOOK_URL` is configured; 404s (not 403)
  for another engineer's record, same as every other `/records/{id}*`
  route. Frontend: a "Resend webhook" button on the record detail page,
  next to the delivery history it just refreshes after a successful
  replay.
- **Frontend test coverage and CI.** The frontend previously had zero
  automated tests and wasn't checked by CI at all - `ci.yml`'s single
  job only ran the backend's `ruff`/`pytest`. Now a `frontend` job
  (Node 20, `npm install`/`lint`/`test`/`build`) runs alongside it,
  and `npm run test` (Vitest + React Testing Library, `jsdom`) covers
  `src/lib/`'s pure functions and one representative component:
  - `passwordRules.test.ts` - each of the 5 rules independently, mirrors
    the backend's real `validate_password` policy.
  - `greeting.test.ts` - the first-name/email fallback.
  - `phone.test.ts` - a real regression case from the actual country-code
    dataset: `+1` (US/Canada) is a literal string prefix of `+1242`
    (Bahamas), and `parsePhone()`'s trailing-space boundary check is
    what keeps `"+1242 5551234"` from being mis-parsed as dial code
    `+1`, number `"242 5551234"`.
  - `ConfirmDialog.test.tsx` - a real rendered-DOM test (not a snapshot):
    open/closed, both buttons' callbacks, and that clicking the backdrop
    dismisses it while clicking the dialog body itself does not.
  The data-fetching pages (`RecordsPage`, `RecordDetailPage`, the auth
  forms) aren't covered yet - see
  [What it doesn't do (yet)](../README.md#what-it-doesnt-do-yet).

  **Known limitation, stated plainly:** `package-lock.json` wasn't
  regenerated against a real `npm install` for this change - this dev
  environment has no Node.js/npm available, only Bun (`bun install`
  resolves the same `package.json` correctly and was used for all local
  verification here; it writes its own `bun.lock`, gitignored, not
  committed as a second source of truth alongside npm's lockfile). CI's
  new frontend job therefore uses `npm install`, not `npm ci` (which
  requires a byte-exact lockfile match and would fail against a
  not-yet-regenerated one) - switching back to `ci` is worth doing once
  the lockfile has gone through one real `npm install`.
- **Webhook delivery retries.** `notify_new_record()` used to make a
  single best-effort POST - a receiver's brief outage (a deploy, a cold
  start, a transient 5xx) meant the notification was simply lost, logged
  but never tried again. It now retries with exponential backoff, up to
  `WEBHOOK_MAX_ATTEMPTS` total tries (default 3, `WEBHOOK_RETRY_BACKOFF_SECONDS`
  as the base delay, doubling each retry). A new `attempt_number` column
  on `webhook_deliveries` (migration `9fd2d1b03bea`, backfilled to 1 for
  every pre-existing row via `server_default` rather than left nullable)
  means one row per attempt, not one row overwritten in place, so
  `GET /records/{id}/webhooks` (now explicitly ordered by `attempted_at`)
  shows the full retry history for a record. The same signed request
  body is replayed on every retry rather than re-signed per attempt.
  Verified against a real local HTTP server that fails its first N
  requests and then succeeds (`tests/test_webhooks.py`'s
  `_FlakyHandler`), not a mocked `httpx.post` - one test proves it
  eventually succeeds and stops retrying once it does, another proves it
  gives up after exactly `webhook_max_attempts` tries and every attempt
  is logged. Runs synchronously inside the same upload request (already
  offloaded to a worker thread) rather than as a background job - no
  queue/broker exists in this project - see
  [What it doesn't do (yet)](../README.md#what-it-doesnt-do-yet) for
  that explicit tradeoff.
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
- Frontend: the records list is fetched once (unfiltered) and status
  filtering, name/email search, and per-column sorting (alphabetic for
  strings, numeric for `amount`, including the Status column) all run
  client-side over that single list via `useMemo`, instead of a fresh API
  call per filter click. A stats panel (total/clean/flagged counts and
  flagged %) is derived the same way.
- Frontend: delete (both the records list and the record detail page) now
  confirms through an in-app `ConfirmDialog` instead of the browser's
  native `confirm()`, and the Delete button has a subtle shadow to read as
  more clearly actionable.
- Frontend: an Account settings page (`/settings`) for editing profile
  fields and changing password, reachable via a profile icon in the header
  (replacing the earlier gear icon). `PATCH /users/me` already supported a
  `password` field (fastapi-users' `BaseUserUpdate`, routed through the
  same `UserManager.validate_password` policy registration uses) - no
  backend change was needed for this.
- Forgot/reset password (`/forgot-password`, `/reset-password?token=...`),
  wired onto fastapi-users' own reset-password router. Emails are sent via
  [Resend](https://resend.com) (`RESEND_API_KEY`, `EMAIL_FROM`) - unset
  locally, which logs the reset link instead of sending it. Both routes
  carry the same strict per-IP rate limit as `/login` and `/register`.
- A `has_password` flag on `users` (new column + backfill migration,
  `auth_models.py`) distinguishes an account with a real, user-chosen
  password from a GitHub-OAuth-only signup (which fastapi-users gives a
  random, nobody-knows-it password internally, so `hashed_password` alone
  can't tell the two apart). `UserManager` sets it on registration and on
  any password change/reset; `/auth/forgot-password` checks it before
  issuing a token, so a GitHub-only account can't have password auth
  bootstrapped onto it through an unauthenticated email link - it gets a
  distinct "signs in with GitHub only" response instead, pointing at
  Settings (see above) for adding a password while already signed in.
- **`GET /records` is now paginated.** It used to return every one of the
  caller's matching records in a single, unbounded response - fine for a
  demo account, a real problem the moment a client upload puts tens of
  thousands of rows behind one engineer (one unbounded query, one
  unbounded JSON body). Now takes `limit` (default 100, server-enforced
  hard cap of 500 - not just a default, a caller can't ask for more) and
  `offset`, and returns `{items, total, limit, offset}` instead of a bare
  array, so a caller can tell how many more rows exist beyond the page it
  got. The frontend's `listRecords()` walks every page automatically
  (500 rows per request) and still returns the full set, so the existing
  client-side filter/search/sort/stats panel needed no changes - the win
  isn't fewer rows fetched, it's that no single request (or the query
  behind it) is ever unbounded, however large an account grows.

### Fixed
- **`config.py`'s claim that Resend's shared `onboarding@resend.dev`
  sender "delivers to any recipient" was wrong.** Found via real
  production sends, not assumed: a forgot-password email to a Gmail
  "+"-tagged address and a plain, different address both silently never
  arrived - traced to Resend's own documented restriction (confirmed at
  [resend.com/docs/knowledge-base/403-error-resend-dev-domain](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain)):
  without a verified custom domain, that sender 403s on any recipient
  other than the Resend account's own email, and `_send_email`'s error
  handling (by design) swallows that the same as any other delivery
  failure - so nothing looked broken from the app's side. Every other
  piece of the flow (token generation, expiry, single-use, the gate it
  protects) was independently confirmed working throughout; only real
  delivery to a third party needs a verified domain, which is a
  Resend/DNS step, not a code change. Comments in `config.py`,
  `.env.example`, and the README corrected to say so plainly. This same
  finding is why strict, required email verification was built and then
  deliberately reverted before release rather than shipped: unlike
  forgot-password, a broken verification email would have permanently
  locked out every real registrant but the account owner - see [What it
  doesn't do (yet)](../README.md#what-it-doesnt-do-yet).
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
- **`logger.info(...)` was silently discarded everywhere in the app**,
  locally and in production - nothing configured logging, so Python's
  root logger sat at its `WARNING` default with zero handlers, and
  uvicorn's own log config only wires up its own loggers. Found while
  adding forgot/reset password (above): the no-`RESEND_API_KEY`
  dev-fallback log line never appeared anywhere, checked in a real
  terminal, not assumed working. Fixed by giving the `databridge` logger
  namespace its own explicit level and handler in `main.py`.

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
