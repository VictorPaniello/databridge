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
