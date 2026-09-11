# Webhook Delivery Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move automatic webhook delivery off the request path into a background queue, make retries of the same logical delivery idempotent, and surface a visible "delivery failed permanently" state — the top 3 improvements from the market-research report.

**Architecture:** A new Postgres-backed queue (`webhook_jobs`) replaces the direct `notify_new_record()` call `ingest_file()` currently makes: ingestion enqueues a job per new record (a fast DB insert), and a new standalone worker process (`scripts/webhook_worker.py`, polling in a loop) claims due jobs with `SELECT ... FOR UPDATE SKIP LOCKED` and delivers them, reusing the existing per-attempt HTTP/signing/audit-log logic (extracted into `deliver_attempt()`). Every attempt of one job — and one manual replay — carries a stable `idempotency_key` a receiver can dedupe by. The job's own `status` column (`pending` / `done` / `dead`) is exposed via a new endpoint and a status badge on the record detail page. No new runtime dependency (no Redis/Celery) — Postgres and SQLAlchemy are already there and this project's throughput doesn't need more.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (typed `Mapped` columns), Alembic, pytest against a real local Postgres test DB, React/TypeScript frontend, existing `httpx`/`hmac` webhook signing.

**Spec:** `.scratch/market-research.md` (items #1–#3 of "Prioritized improvement ideas") plus the design decisions recorded in this document — no separate pre-written spec exists; scope and design tradeoffs were settled during this planning session and are captured inline in each task.

## Global Constraints

- No new runtime dependency for the queue — Postgres-backed (`webhook_jobs` table), not Redis/Celery/RQ. An already-installed dependency (Postgres + SQLAlchemy) solves this at this project's scale.
- New models follow the existing typed `Mapped`/`mapped_column` style (see `src/databridge/models.py`) exactly — no bare `Column()`.
- New Alembic migrations are hand-styled like the existing ones: a docstring on `upgrade()` explaining *why*, not just *what*, and a real `downgrade()`. Every new `NOT NULL` column on a table that may already hold rows gets a `server_default`, never a bare default that would fail against existing data (see `9fd2d1b03bea`'s precedent).
- Tests run against the real local Postgres test DB via the existing `db`/`client` fixtures in `tests/conftest.py` — never mock the DB or the HTTP layer (see `tests/test_webhooks.py`'s existing pattern of a real local `HTTPServer`).
- `ruff check .` and `pytest -v` (the backend CI job) must pass at the end of every task. `npx tsc --noEmit`, `npx eslint src`, and `npx vitest run` (from `frontend/`) must pass at the end of Task 7.
- The manual replay path (`POST /records/{id}/webhooks/replay` → `notify_new_record()`) keeps its exact current observable behavior — synchronous, its own fresh retry sequence — except for the new `idempotency_key` field appearing on its response/payload (Task 5).

---

## File structure

| File | Responsibility |
|---|---|
| `src/databridge/models.py` | + `WebhookJob` ORM model; `idempotency_key` column on both `WebhookJob` and `WebhookDelivery` |
| `alembic/versions/<gen1>_*.py` | Creates `webhook_jobs` (Task 1) |
| `alembic/versions/<gen2>_*.py` | Adds `idempotency_key` to both webhook tables (Task 5) |
| `src/databridge/webhooks.py` | `deliver_attempt()` (one HTTP attempt + audit row — extracted), `enqueue_delivery()` (queue a job), `notify_new_record()` (manual replay, now built on `deliver_attempt()`) |
| `src/databridge/webhook_worker.py` (new) | `process_due_jobs()` — claims and works through due `webhook_jobs` rows |
| `scripts/webhook_worker.py` (new) | Long-lived process entrypoint (poll loop), same pattern as `scripts/backup_db.py`/`retention_sweep.py` but continuous, not one-shot |
| `src/databridge/ingest.py` | Calls `enqueue_delivery()` instead of `notify_new_record()` after a successful ingest |
| `src/databridge/main.py` | New `GET /records/{id}/webhook-status` endpoint; updated `run_in_threadpool` comment |
| `src/databridge/schemas.py` | `WebhookDeliveryOut.idempotency_key`; new `WebhookJobStatusOut` |
| `tests/conftest.py` | `_clean_tables` truncates `webhook_jobs` too |
| `tests/test_webhook_worker.py` (new) | Queue/worker unit tests + the new status-endpoint tests |
| `tests/test_webhooks.py` | Updated to drain the queue after upload; new idempotency-key assertions |
| `README.md` | Architecture section gets the worker process |
| `frontend/src/api/types.ts`, `api/client.ts`, `pages/RecordDetailPage.tsx` | Status badge fed by the new endpoint |

---

### Task 1: `WebhookJob` model + migration

**Files:**
- Create: `alembic/versions/<generated>_add_webhook_jobs_table.py`
- Modify: `src/databridge/models.py`
- Modify: `tests/conftest.py:68-70` (`_clean_tables`)
- Test: `tests/test_webhook_worker.py` (new file)

**Interfaces:**
- Produces: `WebhookJob` (fields: `id: uuid.UUID`, `record_id: uuid.UUID`, `status: str` — `"pending"|"done"|"dead"`, `attempt_number: int`, `available_at: datetime`, `created_at: datetime`), importable from `databridge.models`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_webhook_worker.py`:

```python
"""Background webhook-delivery queue: WebhookJob rows enqueue_delivery()
(webhooks.py) creates and process_due_jobs() (webhook_worker.py) claims
and works through - see webhook_worker.py's module docstring for why
this exists instead of delivering inline during POST /records/upload."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from databridge.models import ClientRecord, WebhookJob


def test_webhook_job_can_be_created_with_expected_defaults(db: Session):
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    job = WebhookJob(record_id=record.id)
    db.add(job)
    db.commit()

    fetched = db.execute(select(WebhookJob).where(WebhookJob.record_id == record.id)).scalar_one()
    assert fetched.status == "pending"
    assert fetched.attempt_number == 1
    assert fetched.available_at is not None
    assert fetched.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webhook_worker.py -v`
Expected: FAIL with `ImportError: cannot import name 'WebhookJob' from 'databridge.models'`

- [ ] **Step 3: Add the `WebhookJob` model**

In `src/databridge/models.py`, add near the top of the file (after the existing `datetime`/`uuid` imports, no new imports needed beyond what's already there — `UTC`/`datetime` come from the `datetime` module already imported):

```python
from datetime import UTC, datetime
```

Replace the existing `from datetime import datetime` import line with the line above, then add this class after `WebhookDelivery`:

```python
class WebhookJob(Base):
    """The queue enqueue_delivery() (webhooks.py) writes to and
    webhook_worker.py's process_due_jobs() claims from - one row per
    record needing an automatic (post-ingest) notification. Separate
    from WebhookDelivery (one row per actual HTTP attempt, written by
    both this queue's worker and the manual replay path) - this table
    tracks *scheduling* (is a notification still owed, and when's the
    next attempt due), not delivery history."""

    __tablename__ = "webhook_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("client_records.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    """"pending" (still owed, waiting for available_at), "done" (delivered
    successfully), or "dead" (every attempt up to settings.
    webhook_max_attempts failed - see webhook_worker.py's
    process_due_jobs()). A plain string, not a DB enum or CHECK
    constraint - enough at this project's scale, the same tradeoff most
    other string columns here already make."""
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    """The attempt about to be made, not the last one that ran - starts
    at 1, incremented only after an attempt fails (see
    process_due_jobs()), so a job that succeeds on its first try never
    advances past 1."""
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    """When this job next becomes eligible to be claimed - now at enqueue
    time, now + backoff after each failed attempt."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Generate and edit the migration**

Run: `alembic revision -m "add webhook_jobs table"`

This creates a new file under `alembic/versions/` with an auto-generated `revision` id. Open it and replace its contents with (keeping the tool-generated `revision` value, do not invent one):

```python
"""add webhook_jobs table

Revision ID: <keep the generated value>
Revises: e986a7123298
Create Date: <keep the generated value>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "<keep the generated value>"
down_revision: Union[str, Sequence[str], None] = "e986a7123298"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    The queue webhooks.py's enqueue_delivery() writes to and
    webhook_worker.py's process_due_jobs() claims from - one row per
    record needing an automatic post-ingest notification, separate from
    webhook_deliveries (one row per HTTP attempt). ondelete="cascade" on
    record_id: deleting a ClientRecord (directly, or transitively via
    DELETE /users/me) must not leave a job pointing at data that no
    longer exists, the same principle every other FK in this project
    already follows.
    """
    op.create_table(
        "webhook_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("client_records.id", ondelete="cascade"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_jobs_record_id", "webhook_jobs", ["record_id"])
    # Matches process_due_jobs()'s claim query (WHERE status = 'pending'
    # AND available_at <= now(), ORDER BY available_at) - without this the
    # worker's poll does a sequential scan of the whole table every
    # interval instead of an index lookup.
    op.create_index(
        "ix_webhook_jobs_status_available_at", "webhook_jobs", ["status", "available_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("webhook_jobs")
```

- [ ] **Step 5: Add `webhook_jobs` to the test-cleanup TRUNCATE list**

In `tests/conftest.py`, `_clean_tables` (around line 68-70), change:

```python
        conn.exec_driver_sql(
            "TRUNCATE webhook_deliveries, client_records, ingestion_runs, oauth_account, users"
        )
```

to:

```python
        conn.exec_driver_sql(
            "TRUNCATE webhook_jobs, webhook_deliveries, client_records, ingestion_runs, "
            "oauth_account, users"
        )
```

- [ ] **Step 6: Run the migration and the test**

Run: `alembic upgrade head && pytest tests/test_webhook_worker.py -v`
Expected: PASS

- [ ] **Step 7: Lint and commit**

Run: `ruff check .`

```bash
git add src/databridge/models.py alembic/versions/*_add_webhook_jobs_table.py tests/conftest.py tests/test_webhook_worker.py
git commit -m "feat: add webhook_jobs queue table"
```

---

### Task 2: Extract `deliver_attempt()` (pure refactor)

Pulls the single-HTTP-attempt logic out of `notify_new_record()`'s retry loop into its own function, so both the manual replay path (unchanged) and the new queue worker (Task 3) can share it instead of duplicating it. No behavior change — every existing test in `tests/test_webhooks.py` must still pass unmodified.

**Files:**
- Modify: `src/databridge/webhooks.py`

**Interfaces:**
- Produces: `deliver_attempt(db: Session, record: ClientRecord, attempt_number: int) -> WebhookDelivery` — builds the payload, signs it, makes one HTTP POST, persists and commits one `WebhookDelivery` row, returns it. Used by both `notify_new_record()` (this task) and `process_due_jobs()` (Task 3).

- [ ] **Step 1: Run the existing suite to confirm the baseline**

Run: `pytest tests/test_webhooks.py -v`
Expected: PASS (12 tests) — this is the regression baseline this refactor must not break.

- [ ] **Step 2: Extract `deliver_attempt()` and rewrite `notify_new_record()`**

In `src/databridge/webhooks.py`, replace the whole `notify_new_record()` function with:

```python
def deliver_attempt(db: Session, record: ClientRecord, attempt_number: int) -> WebhookDelivery:
    """One HTTP attempt: builds and signs the payload, POSTs it, persists
    and commits exactly one WebhookDelivery row recording the outcome.
    Shared by notify_new_record()'s manual-replay retry loop below and
    webhook_worker.py's process_due_jobs() - the only difference between
    an automatic (queued) attempt and a replay's is who calls this and
    how the next attempt (if any) gets scheduled, not what one attempt
    itself does."""
    payload = {
        "event": "client_record.created",
        "record": {
            "id": str(record.id),
            "email": record.email,
            "full_name": record.full_name,
            "has_issues": record.has_issues,
            "issues": record.issues,
        },
    }
    # Serialized once, here - so the signature is computed over the exact
    # bytes that get sent, rather than trusting httpx's own json= encoding
    # to produce identical bytes to whatever we signed separately.
    body = json.dumps(payload).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        headers["X-Databridge-Signature-256"] = sign_payload(body, settings.webhook_secret)

    delivery = WebhookDelivery(
        record_id=record.id, url=settings.webhook_url, success=False, attempt_number=attempt_number
    )
    try:
        response = httpx.post(
            settings.webhook_url, content=body, headers=headers, timeout=TIMEOUT_SECONDS
        )
        delivery.status_code = response.status_code
        delivery.success = response.is_success
        if not response.is_success:
            delivery.error = f"non-2xx response: {response.status_code}"
    except httpx.HTTPError as exc:
        delivery.error = f"{type(exc).__name__}: {exc}"

    db.add(delivery)
    db.commit()
    return delivery


def notify_new_record(db: Session, record: ClientRecord) -> WebhookDelivery | None:
    """Manual, on-demand replay only (POST /records/{id}/webhooks/replay,
    main.py) - the automatic post-ingest notification goes through
    enqueue_delivery() and the background worker instead (see
    webhook_worker.py). Kept synchronous deliberately: a replay is a
    human asking for an immediate resend mid-incident, not something that
    should wait behind the queue's own poll interval."""
    if not settings.webhook_url:
        return None  # no receiver configured - nothing to do, not an error

    delivery: WebhookDelivery | None = None
    for attempt in range(1, settings.webhook_max_attempts + 1):
        delivery = deliver_attempt(db, record, attempt)
        if delivery.success:
            return delivery
        if attempt < settings.webhook_max_attempts:
            time.sleep(_backoff_seconds(attempt))

    # Every attempt failed - the last delivery row (already persisted
    # above, success=False) is the one callers get back, the same
    # contract as before this refactor.
    return delivery
```

(`sign_payload()`, `_backoff_seconds()`, `TIMEOUT_SECONDS`, and the imports already in the file are unchanged.)

- [ ] **Step 3: Run the existing suite again**

Run: `pytest tests/test_webhooks.py -v`
Expected: PASS (12 tests, unchanged) — proves the extraction didn't change behavior.

- [ ] **Step 4: Lint and commit**

Run: `ruff check .`

```bash
git add src/databridge/webhooks.py
git commit -m "refactor: extract deliver_attempt() from notify_new_record()"
```

---

### Task 3: Queue + worker — `enqueue_delivery()`, `process_due_jobs()`, wire into `ingest.py`

This is the core of the feature: automatic post-ingest notification becomes "insert a job row" instead of "make the HTTP call right now," and a new `process_due_jobs()` function does the actual delivering. Existing `tests/test_webhooks.py` tests that assumed synchronous delivery-on-upload need to explicitly drain the queue.

**Files:**
- Modify: `src/databridge/webhooks.py` (add `enqueue_delivery()`, update module docstring)
- Create: `src/databridge/webhook_worker.py`
- Modify: `src/databridge/ingest.py`
- Modify: `src/databridge/main.py:232-244` (comment only)
- Modify: `tests/test_webhooks.py`
- Test: `tests/test_webhook_worker.py` (extend)

**Interfaces:**
- Consumes: `deliver_attempt(db, record, attempt_number) -> WebhookDelivery` (Task 2), `WebhookJob` (Task 1), `_backoff_seconds(attempt: int) -> float` (existing).
- Produces: `enqueue_delivery(db: Session, record: ClientRecord) -> WebhookJob | None` (webhooks.py); `process_due_jobs(db: Session, limit: int = 20) -> int` (webhook_worker.py, returns count of jobs processed) — both consumed by Task 4's script entrypoint and Task 3's own tests.

- [ ] **Step 1: Write the failing tests**

Add these imports to the top of `tests/test_webhook_worker.py`, alongside the existing ones (so the file's imports stay together at the top — `ruff` flags a mid-file import):

```python
import time

from databridge.models import WebhookDelivery
from databridge.webhook_worker import process_due_jobs
from databridge.webhooks import enqueue_delivery
```

Then append these test functions to the end of the file:

```python
def test_enqueue_delivery_creates_a_pending_job(db: Session):
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    job = enqueue_delivery(db, record)
    db.commit()

    assert job is not None
    assert job.status == "pending"
    assert job.record_id == record.id


def test_enqueue_delivery_is_a_noop_without_a_configured_webhook_url(db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", None)
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    assert enqueue_delivery(db, record) is None


def test_process_due_jobs_delivers_a_pending_job_and_marks_it_done(db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")

    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()
    job = enqueue_delivery(db, record)
    db.commit()
    job_id = job.id

    # 127.0.0.1:1 refuses every connection - a real, deterministic failure
    # (not a mock), the same pattern flaky_webhook_receiver uses elsewhere
    # in this project, just via connection refusal instead of an HTTP 500.
    processed = process_due_jobs(db)

    assert processed == 1
    refreshed = db.get(WebhookJob, job_id)
    assert refreshed.status == "pending"  # first attempt failed, not dead yet (max_attempts default 3)
    assert refreshed.attempt_number == 2
    assert refreshed.available_at > datetime.now(UTC)  # scheduled for a future retry

    deliveries = db.execute(
        select(WebhookDelivery).where(WebhookDelivery.record_id == record.id)
    ).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].success is False


def test_process_due_jobs_marks_a_job_dead_after_max_attempts(db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")
    monkeypatch.setattr(webhooks_module.settings, "webhook_max_attempts", 2)
    monkeypatch.setattr(webhooks_module.settings, "webhook_retry_backoff_seconds", 0.01)

    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()
    job = enqueue_delivery(db, record)
    db.commit()
    job_id = job.id

    process_due_jobs(db)  # attempt 1 fails -> scheduled for attempt 2
    time.sleep(0.05)
    process_due_jobs(db)  # attempt 2 fails -> max_attempts reached -> dead

    refreshed = db.get(WebhookJob, job_id)
    assert refreshed.status == "dead"
    assert refreshed.attempt_number == 2
```

- [ ] **Step 2: Run to verify these fail**

Run: `pytest tests/test_webhook_worker.py -v`
Expected: FAIL with `ImportError: cannot import name 'process_due_jobs' from 'databridge.webhook_worker'` (module doesn't exist yet)

- [ ] **Step 3: Add `enqueue_delivery()` to `webhooks.py`**

Add this function to `src/databridge/webhooks.py`, right after `deliver_attempt()`:

```python
def enqueue_delivery(db: Session, record: ClientRecord) -> WebhookJob | None:
    """Called once per newly-inserted record, right after ingest (see
    ingest.py) - replaces what used to be a direct notify_new_record()
    call. Just a fast DB insert, so a slow or dead receiver can never add
    latency to POST /records/upload; the actual HTTP attempt happens
    later, off the request path, in webhook_worker.py's
    process_due_jobs()."""
    if not settings.webhook_url:
        return None  # no receiver configured - nothing to do, not an error
    job = WebhookJob(record_id=record.id)
    db.add(job)
    db.flush()  # assigns job.id
    return job
```

Add `WebhookJob` to the existing `from databridge.models import ClientRecord, WebhookDelivery` import line, making it `from databridge.models import ClientRecord, WebhookDelivery, WebhookJob`.

Update the module docstring at the top of `webhooks.py` to describe the new split:

```python
"""Outbound webhook delivery. A failed delivery must never fail the ingest
request that triggered it - the record is already safely persisted by the
time we attempt to notify anyone, so a network blip on the receiving end is
the receiver's problem to retry, not a reason to roll back real data.

Two delivery paths, both ending in deliver_attempt() below:
- Automatic (post-ingest): ingest.py calls enqueue_delivery() to create a
  WebhookJob row; webhook_worker.py's process_due_jobs() claims and
  delivers it later, off the request path, with retries scheduled via
  the job's own available_at (see WebhookJob in models.py).
- Manual replay (POST /records/{id}/webhooks/replay, main.py):
  notify_new_record() runs its own retry loop synchronously, in the
  request - a human asking for an immediate resend, not queued work.

Both paths persist one WebhookDelivery row per attempt, so the audit
trail shows the full retry history either way."""
```

- [ ] **Step 4: Create `src/databridge/webhook_worker.py`**

```python
"""Background delivery worker for the webhook_jobs queue (webhooks.py's
enqueue_delivery() writes to it, models.py's WebhookJob defines it). Runs
as its own long-lived process (see scripts/webhook_worker.py), separate
from the request/response cycle - the whole reason this exists is so a
slow or dead receiver can never add latency to POST /records/upload (see
main.py's comment on why that route uses run_in_threadpool for the CSV/DB
work that's still done inline).

ponytail: claims one job at a time (SELECT ... FOR UPDATE SKIP LOCKED
LIMIT 1, committed before claiming the next) rather than batching the
claim query - a batched claim would hold every claimed row's lock only
until the *first* job's own commit, silently letting a second worker
process claim the rest of the "still in progress" batch. One-at-a-time
costs an extra round trip per job; worth it for correctness at this
project's throughput. Move to a two-phase claim (mark 'processing' +
commit, then deliver, then commit the final status) if this ever needs
to batch for real throughput."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from databridge.config import settings
from databridge.models import ClientRecord, WebhookJob
from databridge.webhooks import _backoff_seconds, deliver_attempt


def process_due_jobs(db: Session, limit: int = 20) -> int:
    """Claims and works through up to `limit` due jobs (status="pending",
    available_at in the past), one at a time. Returns how many it
    processed (not how many succeeded - a job that failed and was
    rescheduled, or marked dead, still counts)."""
    processed = 0
    for _ in range(limit):
        job = db.execute(
            select(WebhookJob)
            .where(WebhookJob.status == "pending", WebhookJob.available_at <= datetime.now(UTC))
            .order_by(WebhookJob.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            break

        # Guaranteed to exist: ON DELETE CASCADE on webhook_jobs.record_id
        # (see the Task 1 migration) means a DELETE /records/{id} that
        # removes this record has to acquire a lock on this job row too -
        # it blocks behind the FOR UPDATE above until this transaction
        # commits, by which point this job is already done/dead and about
        # to be deleted right along with the record it points at.
        record = db.get(ClientRecord, job.record_id)

        delivery = deliver_attempt(db, record, job.attempt_number)
        if delivery.success:
            job.status = "done"
        elif job.attempt_number >= settings.webhook_max_attempts:
            job.status = "dead"
        else:
            delay = _backoff_seconds(job.attempt_number)
            job.attempt_number += 1
            job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        db.commit()
        processed += 1
    return processed
```

- [ ] **Step 5: Run the new tests**

Run: `pytest tests/test_webhook_worker.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Wire `ingest.py` to enqueue instead of notifying directly**

In `src/databridge/ingest.py`, change the import:

```python
from databridge.webhooks import notify_new_record
```

to:

```python
from databridge.webhooks import enqueue_delivery
```

And change the tail of `ingest_file()` from:

```python
    run.rows_flagged = sum(1 for r in inserted if r.has_issues)
    run.rows_clean = len(inserted) - run.rows_flagged
    run.rows_skipped_existing = skipped_existing

    db.commit()

    for record in inserted:
        notify_new_record(db, record)

    return inserted, run
```

to:

```python
    run.rows_flagged = sum(1 for r in inserted if r.has_issues)
    run.rows_clean = len(inserted) - run.rows_flagged
    run.rows_skipped_existing = skipped_existing

    # Enqueued in the same transaction as the run/records themselves, not
    # after - a job only ever exists for a record that actually made it
    # into the database, never an orphan left behind by a rolled-back
    # ingest.
    for record in inserted:
        enqueue_delivery(db, record)

    db.commit()

    return inserted, run
```

- [ ] **Step 7: Update the now-inaccurate comment in `main.py`**

In `src/databridge/main.py`, inside `upload_records()` (around line 232), replace:

```python
    # ingest_file does CPU-bound CSV parsing, several synchronous DB
    # round-trips, and (via notify_new_record) a blocking httpx.post to the
    # webhook receiver with up to a 5s timeout. This route is `async def`
    # (needed for `await file.read()` above), and FastAPI only auto-offloads
    # *sync* `def` routes to a worker thread - a sync call made directly
    # inside an async route runs straight on the single event loop thread
    # instead, stalling every other in-flight request for as long as it
    # takes. run_in_threadpool moves it off the loop, the same mechanism
    # FastAPI itself uses for sync routes. Found via a deliberate
    # scalability/performance review, not a user report.
```

with:

```python
    # ingest_file does CPU-bound CSV parsing and several synchronous DB
    # round-trips (schema mapping/validation via tidycsv, one INSERT per
    # row) - no longer a blocking httpx.post to the webhook receiver too,
    # since the automatic post-ingest notification is now enqueue_delivery()
    # (a fast DB insert, see webhooks.py/webhook_worker.py) instead of a
    # direct notify_new_record() call, but the CSV/DB work alone still
    # justifies offloading. This route is `async def` (needed for `await
    # file.read()` above), and FastAPI only auto-offloads *sync* `def`
    # routes to a worker thread - a sync call made directly inside an
    # async route runs straight on the single event loop thread instead,
    # stalling every other in-flight request for as long as it takes.
    # run_in_threadpool moves it off the loop, the same mechanism FastAPI
    # itself uses for sync routes. Found via a deliberate scalability/
    # performance review, not a user report.
```

- [ ] **Step 8: Add a drain helper and update the affected tests in `tests/test_webhooks.py`**

Add near the top of `tests/test_webhooks.py` (after the existing imports, alongside the other fixtures):

```python
import time

from sqlalchemy.orm import Session

from databridge.webhook_worker import process_due_jobs


def _drain_jobs(db: Session, max_iterations: int = 20) -> None:
    """Repeatedly processes due webhook jobs until none are left (or
    max_iterations is hit) - a job that just failed becomes due again
    only after its backoff delay, so tests exercising a full retry
    sequence need to give that real wall-clock time to pass, the same
    reason flaky_webhook_receiver below shrinks
    settings.webhook_retry_backoff_seconds to 0.01 instead of the real
    default."""
    for _ in range(max_iterations):
        time.sleep(0.02)
        if process_due_jobs(db) == 0:
            return
```

Then, in each of the following test functions, add a `db: Session` parameter and insert `_drain_jobs(db)` immediately after the upload call and before the first assertion that reads delivery data:

`test_receiver_can_verify_the_real_signature`:
```python
def test_receiver_can_verify_the_real_signature(client: TestClient, db: Session, webhook_receiver):
    response = _upload(client)
    assert response.status_code == 200
    _drain_jobs(db)

    assert webhook_receiver.received, "webhook receiver never got a request"
```
(rest of the function body unchanged)

`test_tampered_body_fails_verification`:
```python
def test_tampered_body_fails_verification(client: TestClient, db: Session, webhook_receiver):
    _upload(client)
    _drain_jobs(db)
    delivery = webhook_receiver.received[0]
```
(rest unchanged)

`test_retries_and_eventually_succeeds_against_a_real_flaky_receiver`:
```python
def test_retries_and_eventually_succeeds_against_a_real_flaky_receiver(
    client: TestClient, db: Session, flaky_webhook_receiver
):
    flaky_webhook_receiver.fail_first_n = 2  # 500, 500, then a real 200

    response = _upload_single_row(client)
    record_id = response.json()["records"][0]["id"]
    _drain_jobs(db)

    assert len(flaky_webhook_receiver.received) == 3  # first try + 2 retries
```
(rest unchanged)

`test_gives_up_after_max_attempts_and_logs_every_one`:
```python
def test_gives_up_after_max_attempts_and_logs_every_one(
    client: TestClient, db: Session, flaky_webhook_receiver
):
    flaky_webhook_receiver.fail_first_n = 999  # never succeeds

    response = _upload_single_row(client)
    record_id = response.json()["records"][0]["id"]
    _drain_jobs(db)

    # settings.webhook_max_attempts defaults to 3 - every one of them was
    # actually tried against the real receiver, not just the first.
    assert len(flaky_webhook_receiver.received) == 3
```
(rest unchanged)

`test_replay_sends_a_fresh_delivery_on_demand`:
```python
def test_replay_sends_a_fresh_delivery_on_demand(client: TestClient, db: Session, webhook_receiver):
    record_id = _upload(client).json()["records"][0]["id"]
    _drain_jobs(db)
    assert len(webhook_receiver.received) == 5  # one per row in messy_clients.csv
```
(rest unchanged)

`test_replay_retries_on_a_transient_failure_the_same_as_a_real_delivery`:
```python
def test_replay_retries_on_a_transient_failure_the_same_as_a_real_delivery(
    client: TestClient, db: Session, flaky_webhook_receiver
):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    _drain_jobs(db)
    assert len(flaky_webhook_receiver.received) == 1  # succeeded first try (fail_first_n=0)
```
(rest unchanged)

The remaining tests (`test_sign_payload_matches_a_reference_hmac_implementation`, `test_replay_without_a_configured_webhook_url_is_rejected`, `test_replay_respects_ownership`, `test_replay_unknown_record_returns_404`) don't assert on automatic-delivery timing and need no change.

- [ ] **Step 9: Run the full backend suite**

Run: `pytest -v`
Expected: PASS (all tests, including the updated `test_webhooks.py` and `test_concurrency.py` — `test_concurrency.py` needs no code change, it only asserts `ingest_file` runs off the event-loop thread, which is still true)

- [ ] **Step 10: Lint and commit**

Run: `ruff check .`

```bash
git add src/databridge/webhooks.py src/databridge/webhook_worker.py src/databridge/ingest.py src/databridge/main.py tests/test_webhooks.py tests/test_webhook_worker.py
git commit -m "feat: deliver webhooks via a background queue instead of inline"
```

---

### Task 4: Worker entrypoint script + README

**Files:**
- Create: `scripts/webhook_worker.py`
- Modify: `README.md` (Architecture section, around line 84-99)

**Interfaces:**
- Consumes: `process_due_jobs(db: Session, limit: int = 20) -> int` (Task 3).

- [ ] **Step 1: Create `scripts/webhook_worker.py`**

```python
#!/usr/bin/env python3
"""Entrypoint for the background webhook-delivery worker - see
databridge.webhook_worker for the actual logic. Unlike scripts/
backup_db.py and scripts/retention_sweep.py (one-shot jobs run on a Cron
Schedule), this runs continuously: deploy it as its own long-lived
Railway service (a Start Command, not a Cron Schedule), restarted by
Railway itself if it ever crashes.

Run manually: python scripts/webhook_worker.py
"""

from __future__ import annotations

import sys
import time

from databridge.db import SessionLocal
from databridge.webhook_worker import process_due_jobs

POLL_INTERVAL_SECONDS = 2.0


def main() -> None:
    print(f"webhook worker started, polling every {POLL_INTERVAL_SECONDS}s")
    while True:
        db = SessionLocal()
        try:
            processed = process_due_jobs(db)
            if processed:
                print(f"processed {processed} webhook job(s)")
        finally:
            db.close()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
```

- [ ] **Step 2: Update the README's Architecture section**

In `README.md`, replace the architecture diagram block (around line 86-99):

```
CSV/Excel upload
      │
      ▼
tidycsv (schema-driven cleaning, validation)
      │
      ▼
PostgreSQL (ingestion_runs, client_records, webhook_deliveries)
      │
      ▼
Outbound webhook (retried with backoff on failure - a failed delivery
                   never fails the ingest, it's retried and logged, and
                   the data is already safely persisted either way)
```

with:

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

Add one sentence right after the diagram (before the existing `config.py holds every environment-dependent value...` paragraph):

```
Automatic post-ingest notification is queued, not sent inline: `ingest_file()`
enqueues one `webhook_jobs` row per new record, and `scripts/webhook_worker.py`
(a separate, continuously-running process - see [Deployment](#deployment))
claims and delivers them. A manual replay (`POST /records/{id}/webhooks/replay`)
is the one exception - it still delivers synchronously in the request, since
that's a human asking for an immediate resend, not queued background work.
```

- [ ] **Step 3: Verify the worker runs against the local dev DB**

Run: `python scripts/webhook_worker.py` (with `DATABASE_URL` pointing at local dev Postgres, `WEBHOOK_URL` unset or pointing at `examples/webhook_receiver.py`), confirm it prints the startup line and doesn't crash; stop it with Ctrl+C.
Expected: prints `webhook worker started, polling every 2.0s`, exits cleanly on Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add scripts/webhook_worker.py README.md
git commit -m "feat: add webhook worker entrypoint script, document the queue in README"
```

---

### Task 5: Idempotency keys

Every attempt of one automatic delivery job (its retries) shares one `idempotency_key`; one manual replay call generates its own, different key. A receiver can use it to safely dedupe at-least-once delivery.

**Files:**
- Create: `alembic/versions/<gen2>_add_idempotency_key.py`
- Modify: `src/databridge/models.py`
- Modify: `src/databridge/webhooks.py`
- Modify: `src/databridge/webhook_worker.py`
- Modify: `src/databridge/schemas.py`
- Modify: `tests/test_webhooks.py`

**Interfaces:**
- Consumes: `WebhookJob`, `WebhookDelivery` (Task 1).
- Produces: `deliver_attempt(db, record, attempt_number, idempotency_key: uuid.UUID) -> WebhookDelivery` (signature change from Task 2/3 — update both call sites).

- [ ] **Step 1: Write the failing tests**

Add `import json` to the top of `tests/test_webhooks.py`, alongside the existing imports (a mid-file import fails `ruff`). Then append these test functions to the end of the file:

```python
def test_retries_of_the_same_job_share_one_idempotency_key(
    client: TestClient, db: Session, flaky_webhook_receiver
):
    flaky_webhook_receiver.fail_first_n = 1  # fails once, then succeeds
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    _drain_jobs(db)

    deliveries = client.get(f"/records/{record_id}/webhooks").json()
    assert len(deliveries) == 2
    assert deliveries[0]["idempotency_key"] == deliveries[1]["idempotency_key"]


def test_replay_gets_a_different_idempotency_key_than_the_automatic_delivery(
    client: TestClient, db: Session, webhook_receiver
):
    record_id = _upload(client).json()["records"][0]["id"]
    _drain_jobs(db)
    original = client.get(f"/records/{record_id}/webhooks").json()[0]["idempotency_key"]

    replay = client.post(f"/records/{record_id}/webhooks/replay").json()
    assert replay["idempotency_key"] != original


def test_idempotency_key_is_included_in_the_signed_payload(
    client: TestClient, db: Session, webhook_receiver
):
    record_id = _upload(client).json()["records"][0]["id"]
    _drain_jobs(db)

    delivery = client.get(f"/records/{record_id}/webhooks").json()[0]
    received_body = json.loads(webhook_receiver.received[0]["body"])
    assert received_body["idempotency_key"] == delivery["idempotency_key"]
```

- [ ] **Step 2: Run to verify these fail**

Run: `pytest tests/test_webhooks.py -k idempotency -v`
Expected: FAIL with a `KeyError`/`AssertionError` on `"idempotency_key"` — the field doesn't exist yet.

- [ ] **Step 3: Generate and edit the migration**

Run: `alembic revision -m "add idempotency_key to webhook tables"`

Replace the generated file's contents with (`down_revision` is the revision id generated in Task 1's migration):

```python
"""add idempotency_key to webhook tables

Revision ID: <keep the generated value>
Revises: <Task 1's generated revision id>
Create Date: <keep the generated value>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "<keep the generated value>"
down_revision: Union[str, Sequence[str], None] = "<Task 1's generated revision id>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    idempotency_key ties every attempt of one logical notification
    together: every retry of one enqueue_delivery() job shares the value
    generated when the job was created, and one notify_new_record()
    replay call generates its own - a receiver can dedupe by it
    regardless of which attempt actually got through first (see
    webhooks.py's deliver_attempt()). Both tables may already have real
    rows by the time this runs (webhook_deliveries always might;
    webhook_jobs was only just introduced in this same change set, but
    treating it the same way costs nothing and avoids relying on that
    always being true), so both get a real per-row backfill value, not
    just a NOT NULL default that would fail against existing data -
    gen_random_uuid() (built into Postgres core since v13, no extension
    needed) is evaluated per row during the ALTER, giving every
    pre-existing row its own distinct key. Same reasoning as
    attempt_number's server_default='1' in 9fd2d1b03bea, just a
    generated value instead of a constant one.
    """
    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "idempotency_key",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.add_column(
        "webhook_jobs",
        sa.Column(
            "idempotency_key",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("webhook_jobs", "idempotency_key")
    op.drop_column("webhook_deliveries", "idempotency_key")
```

- [ ] **Step 4: Add the column to both models**

In `src/databridge/models.py`, add to `WebhookDelivery` (after `attempt_number`):

```python
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    """Shared by every attempt of the same logical notification - every
    retry of one webhook_jobs row (or one notify_new_record() replay
    call) reuses the same value, so a receiver can dedupe at-least-once
    delivery. See webhooks.py's deliver_attempt()."""
```

And to `WebhookJob` (after `id`):

```python
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    """Generated once, at enqueue time - every attempt process_due_jobs()
    makes for this job reuses this same value (passed into
    deliver_attempt()), so retries of one job are recognizable as the
    same logical notification, not independent ones."""
```

- [ ] **Step 5: Thread `idempotency_key` through `deliver_attempt()`**

In `src/databridge/webhooks.py`, change `deliver_attempt()`'s signature and payload:

```python
def deliver_attempt(
    db: Session, record: ClientRecord, attempt_number: int, idempotency_key: uuid.UUID
) -> WebhookDelivery:
    """One HTTP attempt: builds and signs the payload, POSTs it, persists
    and commits exactly one WebhookDelivery row recording the outcome.
    idempotency_key is shared across every attempt of the same logical
    notification (see WebhookDelivery/WebhookJob's docstrings in
    models.py) - the caller decides what that value is, this function
    just carries it through to both the payload and the audit row.
    Shared by notify_new_record()'s manual-replay retry loop below and
    webhook_worker.py's process_due_jobs()."""
    payload = {
        "event": "client_record.created",
        "idempotency_key": str(idempotency_key),
        "record": {
            "id": str(record.id),
            "email": record.email,
            "full_name": record.full_name,
            "has_issues": record.has_issues,
            "issues": record.issues,
        },
    }
    body = json.dumps(payload).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        headers["X-Databridge-Signature-256"] = sign_payload(body, settings.webhook_secret)

    delivery = WebhookDelivery(
        record_id=record.id,
        url=settings.webhook_url,
        success=False,
        attempt_number=attempt_number,
        idempotency_key=idempotency_key,
    )
    try:
        response = httpx.post(
            settings.webhook_url, content=body, headers=headers, timeout=TIMEOUT_SECONDS
        )
        delivery.status_code = response.status_code
        delivery.success = response.is_success
        if not response.is_success:
            delivery.error = f"non-2xx response: {response.status_code}"
    except httpx.HTTPError as exc:
        delivery.error = f"{type(exc).__name__}: {exc}"

    db.add(delivery)
    db.commit()
    return delivery
```

Add `import uuid` to the top of `webhooks.py` if not already present (it isn't).

Update `notify_new_record()` to generate one key and pass it through every attempt:

```python
def notify_new_record(db: Session, record: ClientRecord) -> WebhookDelivery | None:
    """Manual, on-demand replay only (POST /records/{id}/webhooks/replay,
    main.py) - the automatic post-ingest notification goes through
    enqueue_delivery() and the background worker instead (see
    webhook_worker.py). Kept synchronous deliberately: a replay is a
    human asking for an immediate resend mid-incident, not something that
    should wait behind the queue's own poll interval. Generates its own
    idempotency_key, shared by every attempt of *this* replay's own
    retry loop - deliberately different from the automatic delivery's
    key, since a replay is a new, intentional resend a receiver should
    process, not a duplicate to silently drop."""
    if not settings.webhook_url:
        return None

    idempotency_key = uuid.uuid4()
    delivery: WebhookDelivery | None = None
    for attempt in range(1, settings.webhook_max_attempts + 1):
        delivery = deliver_attempt(db, record, attempt, idempotency_key)
        if delivery.success:
            return delivery
        if attempt < settings.webhook_max_attempts:
            time.sleep(_backoff_seconds(attempt))

    return delivery
```

- [ ] **Step 6: Update `process_due_jobs()` to pass the job's key**

In `src/databridge/webhook_worker.py`, change the `deliver_attempt()` call:

```python
        delivery = deliver_attempt(db, record, job.attempt_number, job.idempotency_key)
```

- [ ] **Step 7: Expose it in the API**

In `src/databridge/schemas.py`, add to `WebhookDeliveryOut` (after `attempt_number`):

```python
    idempotency_key: uuid.UUID
```

- [ ] **Step 8: Run the tests**

Run: `pytest tests/test_webhooks.py tests/test_webhook_worker.py -v`
Expected: PASS (`test_process_due_jobs_delivers_a_pending_job_and_marks_it_done` and `test_process_due_jobs_marks_a_job_dead_after_max_attempts` from Task 3 need no change - they use `enqueue_delivery()`/`process_due_jobs()` directly, which already generate and thread the key correctly once Steps 4-6 land)

- [ ] **Step 9: Full suite, lint, commit**

Run: `pytest -v && ruff check .`

```bash
git add src/databridge/models.py src/databridge/webhooks.py src/databridge/webhook_worker.py src/databridge/schemas.py alembic/versions/*_add_idempotency_key.py tests/test_webhooks.py
git commit -m "feat: idempotency keys on webhook deliveries"
```

---

### Task 6: Dead-letter visible status — backend

Exposes the job's `status` (`pending`/`done`/`dead`/`not_configured`) per record, so a permanently-failed delivery is visible instead of silently sitting there.

**Files:**
- Modify: `src/databridge/schemas.py`
- Modify: `src/databridge/main.py`
- Modify: `tests/test_webhook_worker.py`

**Interfaces:**
- Produces: `GET /records/{record_id}/webhook-status` → `WebhookJobStatusOut` (`status: str`, `attempt_number: int | None`, `available_at: datetime | None`).

- [ ] **Step 1: Write the failing tests**

Add `from fastapi.testclient import TestClient` to the top of `tests/test_webhook_worker.py`, alongside the existing imports (a mid-file import fails `ruff`). Then append these to the end of the file:

```python
def _upload_single_row(client: TestClient):
    csv_body = (
        "Customer,Contact Email,Order Date,Order Total,Mobile Number\n"
        "Ada Lovelace,ada@shop.com,2026-09-01,100.00,+34 600 00 00 00\n"
    )
    return client.post(
        "/records/upload", files={"file": ("single.csv", csv_body.encode(), "text/csv")}
    )


def test_webhook_status_is_not_configured_without_a_webhook_url(client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = client.get(f"/records/{record_id}/webhook-status")
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


def test_webhook_status_is_pending_before_the_worker_runs(client: TestClient, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    response = client.get(f"/records/{record_id}/webhook-status")
    assert response.json()["status"] == "pending"
    assert response.json()["attempt_number"] == 1


def test_webhook_status_is_dead_after_every_attempt_fails(client: TestClient, db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")
    monkeypatch.setattr(webhooks_module.settings, "webhook_max_attempts", 1)
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    process_due_jobs(db)

    response = client.get(f"/records/{record_id}/webhook-status")
    assert response.json()["status"] == "dead"


def test_webhook_status_respects_ownership(client: TestClient, other_client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = other_client.get(f"/records/{record_id}/webhook-status")
    assert response.status_code == 404
```

- [ ] **Step 2: Run to verify these fail**

Run: `pytest tests/test_webhook_worker.py -k webhook_status -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Add the schema**

In `src/databridge/schemas.py`, add:

```python
class WebhookJobStatusOut(BaseModel):
    """The automatic (post-ingest) delivery pipeline's current state for
    one record - distinct from the per-attempt history
    GET /records/{id}/webhooks returns. "not_configured" when no
    WEBHOOK_URL is set at all (mirrors enqueue_delivery()'s no-op in that
    case) rather than pretending a job exists that was never created.
    Doesn't reflect manual replays - those are a deliberate one-off
    action outside this pipeline (see notify_new_record()'s docstring)."""

    status: str  # "pending" | "done" | "dead" | "not_configured"
    attempt_number: int | None
    available_at: datetime | None
```

- [ ] **Step 4: Add the endpoint**

In `src/databridge/main.py`, add after `get_record_webhooks()`:

```python
@app.get("/records/{record_id}/webhook-status", response_model=WebhookJobStatusOut)
def get_record_webhook_status(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> WebhookJobStatusOut:
    """The automatic post-ingest delivery pipeline's current state for one
    record - see WebhookJobStatusOut's docstring for what each status
    means."""
    _get_owned_record(db, record_id, user)
    job = db.execute(select(WebhookJob).where(WebhookJob.record_id == record_id)).scalar_one_or_none()
    if job is None:
        return WebhookJobStatusOut(status="not_configured", attempt_number=None, available_at=None)
    return WebhookJobStatusOut(
        status=job.status, attempt_number=job.attempt_number, available_at=job.available_at
    )
```

Add `WebhookJob` to the existing `from databridge.models import ClientRecord, IngestionRun, WebhookDelivery` import, making it `from databridge.models import ClientRecord, IngestionRun, WebhookDelivery, WebhookJob`. Add `WebhookJobStatusOut` to the existing `from databridge.schemas import (...)` import block.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_webhook_worker.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 6: Full suite, lint, commit**

Run: `pytest -v && ruff check .`

```bash
git add src/databridge/schemas.py src/databridge/main.py tests/test_webhook_worker.py
git commit -m "feat: expose webhook delivery status via GET /records/{id}/webhook-status"
```

---

### Task 7: Dead-letter visible status — frontend badge

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/RecordDetailPage.tsx`

**Interfaces:**
- Consumes: `GET /records/{id}/webhook-status` (Task 6).
- Produces: `getRecordWebhookStatus(id: string): Promise<WebhookJobStatus>` in `api/client.ts`.

- [ ] **Step 1: Add the type**

In `frontend/src/api/types.ts`, add:

```typescript
// The automatic (post-ingest) delivery pipeline's current state for one
// record - see the backend's WebhookJobStatusOut docstring for what each
// status means. Doesn't reflect manual replays.
export interface WebhookJobStatus {
  status: "pending" | "done" | "dead" | "not_configured";
  attempt_number: number | null;
  available_at: string | null;
}
```

- [ ] **Step 2: Add the API call**

In `frontend/src/api/client.ts`, add near `getRecordWebhooks`:

```typescript
export async function getRecordWebhookStatus(id: string): Promise<WebhookJobStatus> {
  return request<WebhookJobStatus>(`/records/${id}/webhook-status`);
}
```

Add `WebhookJobStatus` to the existing `import type { ... } from "./types"` block at the top of the file.

- [ ] **Step 3: Fetch it in `RecordDetailPage.tsx` and render a badge**

In `frontend/src/pages/RecordDetailPage.tsx`, add to the imports:

```typescript
import type { ClientRecord, WebhookDelivery, WebhookJobStatus } from "../api/types";
```

(replacing the existing `import type { ClientRecord, WebhookDelivery } from "../api/types";`)

Add state:

```typescript
  const [webhookStatus, setWebhookStatus] = useState<WebhookJobStatus | null>(null);
```

Extend the existing `Promise.all` in the load effect:

```typescript
    Promise.all([api.getRecord(id), api.getRecordWebhooks(id), api.getRecordWebhookStatus(id)])
      .then(([recordResult, webhooksResult, statusResult]) => {
        setRecord(recordResult);
        setWebhooks(webhooksResult);
        setWebhookStatus(statusResult);
      })
```

Add a small badge component and render it next to the "Webhook deliveries" heading. Replace:

```jsx
      <div className="mt-8">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold">Webhook deliveries</h2>
```

with:

```jsx
      <div className="mt-8">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Webhook deliveries</h2>
            <WebhookStatusBadge status={webhookStatus} />
          </div>
```

Add this function at the bottom of the file, alongside the existing `Field` helper:

```typescript
function WebhookStatusBadge({ status }: { status: WebhookJobStatus | null }) {
  if (!status || status.status === "not_configured") return null;

  if (status.status === "dead") {
    return (
      <span className="rounded-full bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400 px-2 py-0.5 text-xs">
        Delivery failed permanently
      </span>
    );
  }
  if (status.status === "done") {
    return (
      <span className="rounded-full bg-accent text-accent-foreground px-2 py-0.5 text-xs">
        Delivered
      </span>
    );
  }
  // "pending"
  return (
    <span className="rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400 px-2 py-0.5 text-xs">
      {status.attempt_number && status.attempt_number > 1
        ? `Retrying (attempt ${status.attempt_number})`
        : "Delivery pending"}
    </span>
  );
}
```

- [ ] **Step 4: Verify**

Run: `npx tsc --noEmit && npx eslint src && npx vitest run` (from `frontend/`)
Expected: all clean/passing (no existing test file covers `RecordDetailPage` — this page has no automated test today, consistent with the rest of the page components; verify manually by running the dev server, uploading a file with `WEBHOOK_URL` unset, and unset/misconfigured, and confirming the badge does/doesn't appear as expected).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/pages/RecordDetailPage.tsx
git commit -m "feat: show webhook delivery status badge on record detail page"
```

---

## Spec coverage check

- Background queue for webhook delivery → Tasks 1, 3, 4.
- Idempotency keys on the webhook payload → Task 5.
- Dead-letter/visible failure state → Tasks 3 (job.status="dead"), 6 (API), 7 (UI).

All three top-ranked improvements from `.scratch/market-research.md` are covered end to end (schema → backend → test →, for the status feature, UI).
