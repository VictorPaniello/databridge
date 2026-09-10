# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
nothing has been tagged as a release yet, so everything below is under
`[Unreleased]`.

## [Unreleased]

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
- **`JWT_SECRET` was never set in Railway** - production was signing every
  login token with the obviously-fake default from `config.py`
  (`insecure-local-dev-secret-do-not-use-in-production`), which is right
  there in the source. Confirmed exploitable, not just theoretical: forged
  a JWT for a real user with that known default and it authenticated
  successfully against the live `/users/me`. Fixed by generating a real
  random secret and setting it in Railway - re-verified afterwards that
  the forged token now gets 401 and a fresh real login still works. Found
  by a deliberate security review, not by a user report.
- Registration accepts a one-character password - `UserManager` doesn't
  override `validate_password`, so fastapi-users applies no minimum
  strength requirement. Not yet fixed - tracked in "What it doesn't do
  (yet)".
- No rate limiting on `/auth/jwt/login` or `/auth/register` - brute-force
  and credential-stuffing are currently unthrottled. Not yet fixed -
  tracked in "What it doesn't do (yet)".
- No file size limit on `/records/upload` - an oversized file is read
  fully into memory before tidycsv/pandas ever sees it. Not yet fixed -
  tracked in "What it doesn't do (yet)".

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
