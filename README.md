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
repo), not by copy-pasting its logic — and finding and fixing two real bugs
while doing that integration is part of the story, not something to hide
(see [Bugs found while building this](#bugs-found-while-building-this)
below).

## API

| Method | Path | What it does |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/records/upload` | Upload a CSV/Excel file, clean + persist it, fire webhooks for new records |
| `GET` | `/records` | List records, optionally `?has_issues=true/false` |
| `GET` | `/records/{id}` | Fetch one record |
| `GET` | `/records/{id}/webhooks` | Audit log of webhook delivery attempts for one record |

Re-uploading a file already ingested (matched by email, the schema's key
column) is a no-op, not a duplicate insert or an error.

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

## Local development

Requires a running PostgreSQL instance.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env  # edit DATABASE_URL if needed

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
`WEBHOOK_URL`, `WEBHOOK_SECRET`) are set in Railway's dashboard, never
committed.

On this project, Railway's own `${{Postgres.DATABASE_URL}}` service
reference consistently resolved to an empty string at runtime (confirmed via
`sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL`), no matter
how it was entered (typed, picked from the reference dropdown, or via the
Raw Editor) - tried and ruled out as the cause before working around it.
Building the URL from Postgres's individual `PGUSER`/`PGPASSWORD`/`PGHOST`/
`PGPORT`/`PGDATABASE` variables instead resolved correctly:
`postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`

## Bugs found while building this

Reused `tidycsv` here instead of rewriting its cleaning logic, and that
reuse surfaced two real bugs - fixed at the source (in `tidycsv`, with a
regression test) rather than worked around silently:

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

## What it doesn't do (yet)

- Single schema for the whole service - a real multi-tenant version would
  need a schema per client, not one shared `examples/schema.yaml`.
- No Alembic migrations - tables are created with
  `Base.metadata.create_all()` on startup, fine for this project's scope,
  not how a larger production system should manage schema changes.
- No webhook retry logic - a failed delivery is logged, not automatically
  retried.
- No authentication on the API itself (the webhook *sends* a shared secret,
  but nothing currently guards the upload/read endpoints).

## License

MIT
