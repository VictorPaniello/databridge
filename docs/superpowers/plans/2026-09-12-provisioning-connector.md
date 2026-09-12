# Provisioning Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a record is cleaned and persisted, tidybridge can actually provision it - create the corresponding user on a configured downstream system via a SCIM-shaped `POST /Users` call - not just notify that it exists.

**Architecture:** A second job queue (`ProvisioningJob`/`ProvisioningAttempt`), structurally mirroring the existing `WebhookJob`/`WebhookDelivery` queue and reusing its worker process, backoff helper, and ownership/routing patterns. New `provisioning.py` module owns mapping/payload-building and delivery; `webhook_worker.py` gains a second claim-and-process pass; three new endpoints and a new `RecordDetailPage` section expose it the same way webhooks already are.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, httpx, PyYAML (already an installed transitive dependency via `tidycsv`, not a new one), pytest against real Postgres, React/TypeScript frontend.

**Spec:** `docs/superpowers/specs/2026-09-12-provisioning-connector-design.md`

## Global Constraints

- Generic, configurable REST connector - not hardcoded to one named product.
- New, separate mechanism alongside the existing webhook - never overloads `WebhookJob`/`WebhookDelivery`.
- Deployment-time YAML/env config only - no DB/UI-configurable connector settings.
- Fires automatically right after ingest, same hook point as `enqueue_delivery()`.
- SCIM-shaped by default (`userName`, `name.givenName`/`familyName`, `emails[]`, `active`) - not full SCIM protocol compliance.
- `409 Conflict` is terminal success-equivalent (`status="skipped_exists"`), never retried.
- Out of scope: full SCIM compliance, multi-connector framework, DB/UI-configurable settings, CSV export columns for provisioning, OAuth2 auth, creating real tidybridge `User` accounts from CSV rows.

**Clarifications resolved during planning (spec didn't pin these down explicitly):**

1. **`ProvisioningAttempt` columns:** the spec's table literally lists `id, record_id, attempt_number, status_code, success, error, attempted_at` while also saying it "mirrors `WebhookDelivery` exactly" - but `WebhookDelivery` also has `url` and `idempotency_key`, both omitted from that table. This plan includes both columns, matching the spec's own stated intent over its literal (incomplete) list.
2. **Config YAML's `${PROVISIONING_URL}`/`${PROVISIONING_API_KEY}` templating:** the spec's example mapping file embeds env-var substitution inside `target_url`/`auth_header`. Building a template engine for that would duplicate what `Settings` (pydantic-settings) already does by reading env vars directly - exactly the precedent `webhook_url`/`webhook_secret` already set. So `settings.provisioning_url`/`settings.provisioning_api_key` are read directly (two settings, as the spec's prose says), and the YAML mapping file holds only the `mapping:` dict - no `target_url`/`auth_header` keys, no template engine.
3. **`active: "true"` in the mapping:** every other mapping value is a `ClientRecord` attribute name (looked up via `getattr`). `"true"` isn't a field on `ClientRecord` - it's quoted in the YAML specifically so PyYAML doesn't coerce it to a real bool itself (which would make it indistinguishable from a field-name string). `build_scim_payload()` special-cases the literal strings `"true"`/`"false"` as literal booleans instead of field lookups.
4. **`POST .../replay` response shape:** the spec says this "manually enqueue[s] a fresh attempt" (unlike `replay_webhook`, which runs its retry loop synchronously and returns the resulting delivery) - nothing has actually run by the time this endpoint returns, so there's no attempt result to hand back. It returns `ProvisioningJobStatusOut` (the freshly-reset job's state) instead.

---

### Task 1: Data model - `ProvisioningJob` and `ProvisioningAttempt`

**Files:**
- Modify: `src/tidybridge/models.py` (append after `WebhookJob`)
- Create: `alembic/versions/bef5b3fffd6e_add_provisioning_tables.py`
- Modify: `tests/conftest.py:69-71` (TRUNCATE list)
- Test: `tests/test_provisioning_worker.py` (new file)

**Interfaces:**
- Produces: `ProvisioningJob` (`id: uuid.UUID`, `idempotency_key: uuid.UUID`, `record_id: uuid.UUID`, `status: str`, `attempt_number: int`, `available_at: datetime`, `remote_id: str | None`, `created_at: datetime`) and `ProvisioningAttempt` (`id: int`, `record_id: uuid.UUID`, `url: str`, `status_code: int | None`, `success: bool`, `error: str | None`, `attempt_number: int`, `idempotency_key: uuid.UUID`, `attempted_at: datetime`) - both imported from `tidybridge.models` by every later task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provisioning_worker.py
"""Background provisioning queue: ProvisioningJob rows enqueue_provisioning()
(provisioning.py) creates and process_due_provisioning_jobs()
(webhook_worker.py) claims and works through - see webhook_worker.py's
module docstring for the shared claim pattern, and the spec's "409 is
terminal success" rationale for why this isn't just a copy of the
webhook queue."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tidybridge.models import ClientRecord, ProvisioningJob


def test_provisioning_job_can_be_created_with_expected_defaults(db: Session):
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    job = ProvisioningJob(record_id=record.id)
    db.add(job)
    db.commit()

    fetched = db.execute(
        select(ProvisioningJob).where(ProvisioningJob.record_id == record.id)
    ).scalar_one()
    assert fetched.status == "pending"
    assert fetched.attempt_number == 1
    assert fetched.available_at is not None
    assert fetched.remote_id is None
    assert fetched.idempotency_key is not None
    assert fetched.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provisioning_worker.py -v`
Expected: FAIL with `ImportError: cannot import name 'ProvisioningJob' from 'tidybridge.models'`

- [ ] **Step 3: Add the models**

Append to `src/tidybridge/models.py` (after `WebhookJob`, same imports already at the top of the file cover everything needed - no new imports):

```python
class ProvisioningAttempt(Base):
    """Audit log: every HTTP attempt to provision a record on the
    configured downstream system, whether it succeeded, hit a 409
    (already exists), or failed. Mirrors WebhookDelivery's shape
    exactly - see the plan's Global Constraints for why this includes
    `url`/`idempotency_key` despite the spec's own table listing
    omitting them."""

    __tablename__ = "provisioning_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_records.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """True for both 2xx and 409 - "success" here means "resolved, no
    more attempts needed", matching ProvisioningJob.status's done/
    skipped_exists both being terminal (see process_due_provisioning_jobs
    in webhook_worker.py)."""
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProvisioningJob(Base):
    """The queue enqueue_provisioning() (provisioning.py) writes to and
    webhook_worker.py's process_due_provisioning_jobs() claims from -
    one row per record needing automatic (post-ingest) provisioning.
    Same scheduling-only shape as WebhookJob, plus remote_id: the user
    id the target system hands back on success, needed for any future
    update/dedup, which is exactly why this doesn't fit
    ProvisioningAttempt's per-attempt audit shape."""

    __tablename__ = "provisioning_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("client_records.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    """"pending" | "done" | "skipped_exists" (a 409 - the user already
    exists on the target system, treated as resolved, not a failure) |
    "dead" (every attempt up to settings.webhook_max_attempts failed)."""
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    remote_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Write the migration**

```python
# alembic/versions/bef5b3fffd6e_add_provisioning_tables.py
"""add provisioning_jobs and provisioning_attempts tables

Revision ID: bef5b3fffd6e
Revises: 34debd43d8f8
Create Date: 2026-09-12 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "bef5b3fffd6e"
down_revision: Union[str, Sequence[str], None] = "34debd43d8f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Same shape as webhook_jobs/webhook_deliveries (ec505ec49be8,
    34debd43d8f8) - a scheduling row (provisioning_jobs) plus a
    per-attempt audit log (provisioning_attempts). ondelete="cascade" on
    both record_id columns, same reasoning as every other FK onto
    client_records in this project: deleting a record must not leave a
    job or attempt row pointing at data that no longer exists.
    """
    op.create_table(
        "provisioning_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "idempotency_key",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("client_records.id", ondelete="cascade"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remote_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_provisioning_jobs_record_id", "provisioning_jobs", ["record_id"])
    op.create_index(
        "ix_provisioning_jobs_status_available_at",
        "provisioning_jobs",
        ["status", "available_at"],
    )

    op.create_table(
        "provisioning_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("client_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "idempotency_key",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("provisioning_attempts")
    op.drop_table("provisioning_jobs")
```

- [ ] **Step 5: Update conftest.py's TRUNCATE list**

In `tests/conftest.py`, the `_clean_tables` fixture's single `TRUNCATE` statement must list every table with a live FK, or Postgres refuses to truncate any of them (see that fixture's own comment). Change:

```python
        conn.exec_driver_sql(
            "TRUNCATE webhook_jobs, webhook_deliveries, client_records, ingestion_runs, "
            "oauth_account, users"
        )
```

to:

```python
        conn.exec_driver_sql(
            "TRUNCATE webhook_jobs, webhook_deliveries, provisioning_jobs, "
            "provisioning_attempts, client_records, ingestion_runs, oauth_account, users"
        )
```

- [ ] **Step 6: Run migration and test to verify it passes**

Run: `alembic upgrade head && pytest tests/test_provisioning_worker.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/tidybridge/models.py alembic/versions/bef5b3fffd6e_add_provisioning_tables.py \
  tests/conftest.py tests/test_provisioning_worker.py
git commit -m "feat: add ProvisioningJob and ProvisioningAttempt tables"
```

(Assumes `feat/provisioning-connector` is already checked out - branch off `main` first if not.)

---

### Task 2: Config settings and the default mapping file

**Files:**
- Modify: `src/tidybridge/config.py` (append after `webhook_retry_backoff_seconds`, before `schema_path`)
- Create: `examples/provisioning_mapping.yaml`

**Interfaces:**
- Produces: `settings.provisioning_url: str | None`, `settings.provisioning_api_key: str | None`, `settings.provisioning_mapping_path: str` - consumed by Task 3's `provisioning.py`.

No dedicated test here - these are plain `pydantic-settings` fields with defaults, exercised for real by Task 3's mapping-loader test against the actual default file this step creates.

- [ ] **Step 1: Add the settings**

In `src/tidybridge/config.py`, insert after the `webhook_retry_backoff_seconds` docstring block (before `schema_path`):

```python
    provisioning_url: str | None = None
    """Where to POST a SCIM-shaped user-creation request when a new
    record is ingested. If unset, provisioning is skipped entirely -
    same disable convention as webhook_url. A separate mechanism from
    the webhook (see the provisioning connector spec): the webhook
    notifies that a record arrived, this one actually creates the
    corresponding user on a configured downstream system."""
    provisioning_api_key: str | None = None
    """Sent as `Authorization: Bearer <key>` on every provisioning
    request (see provisioning.py) - a static bearer token, the realistic
    default for most SCIM implementations. None sends no Authorization
    header at all, the same convention webhook_secret uses."""
    provisioning_mapping_path: str = "examples/provisioning_mapping.yaml"
    """Path to the field-mapping YAML (see provisioning.py's
    build_scim_payload), resolved relative to the process's working
    directory - same convention and same reason as schema_path above."""
```

- [ ] **Step 2: Create the default mapping file**

```yaml
# examples/provisioning_mapping.yaml
# Maps ClientRecord fields onto a SCIM core-User POST /Users body. Each
# value is a ClientRecord attribute name, looked up via getattr - except
# the literal strings "true"/"false" (quoted so PyYAML doesn't coerce
# them to a real bool, which would make them indistinguishable from a
# field name), used as literal values instead. See build_scim_payload()
# in provisioning.py.
mapping:
  userName: email
  name.givenName: full_name  # split on first space - documented limitation
  name.familyName: full_name
  emails[0].value: email
  active: "true"
```

- [ ] **Step 3: Commit**

```bash
git add src/tidybridge/config.py examples/provisioning_mapping.yaml
git commit -m "feat: add provisioning connector settings and default mapping"
```

---

### Task 3: Field mapping and SCIM payload building

**Files:**
- Create: `src/tidybridge/provisioning.py`
- Test: `tests/test_provisioning.py` (new file)

**Interfaces:**
- Consumes: `settings.provisioning_mapping_path` (Task 2), `ClientRecord` (`tidybridge.models`, existing).
- Produces: `build_scim_payload(record: ClientRecord, mapping: dict[str, str]) -> dict` and `_load_mapping() -> dict[str, str]` - both used by Task 4's `deliver_provisioning_attempt`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provisioning.py
"""SCIM-shaped payload building from a configured field mapping - see
provisioning.py's build_scim_payload docstring for the "true"/"false"
literal special-case and the full_name-split-on-first-space limitation,
both called out explicitly in the spec."""

from __future__ import annotations

import uuid

from tidybridge.models import ClientRecord
from tidybridge.provisioning import _load_mapping, build_scim_payload


def test_default_mapping_file_loads_the_documented_scim_shape():
    mapping = _load_mapping()
    assert mapping == {
        "userName": "email",
        "name.givenName": "full_name",
        "name.familyName": "full_name",
        "emails[0].value": "email",
        "active": "true",
    }


def test_build_scim_payload_maps_fields_into_the_documented_shape():
    record = ClientRecord(
        id=uuid.uuid4(),
        source_file="test.csv",
        full_name="Grace Hopper",
        email="grace@example.com",
    )
    mapping = _load_mapping()

    payload = build_scim_payload(record, mapping)

    assert payload == {
        "userName": "grace@example.com",
        "name": {"givenName": "Grace", "familyName": "Hopper"},
        "emails": [{"value": "grace@example.com"}],
        "active": True,
    }


def test_build_scim_payload_splits_a_single_word_name_on_both_parts():
    # "documented limitation" (spec) - a name with no space has nothing
    # to put in familyName, so it repeats into both.
    record = ClientRecord(id=uuid.uuid4(), source_file="test.csv", full_name="Cher", email="c@e.com")
    mapping = _load_mapping()

    payload = build_scim_payload(record, mapping)

    assert payload["name"] == {"givenName": "Cher", "familyName": "Cher"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provisioning.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tidybridge.provisioning'`

- [ ] **Step 3: Write the implementation**

```python
# src/tidybridge/provisioning.py
"""Outbound user provisioning: after a record is ingested, actually
create the corresponding user on a configured downstream system via a
SCIM-shaped POST /Users, instead of only notifying that it exists (see
webhooks.py). Same two-path shape as webhooks.py:
- Automatic (post-ingest): ingest.py calls enqueue_provisioning() to
  create a ProvisioningJob row; webhook_worker.py's
  process_due_provisioning_jobs() claims and delivers it later.
- Manual replay (POST /records/{id}/provisioning/replay, main.py):
  replay_provisioning() resets/creates the job row for the worker to
  pick up - unlike webhooks.py's notify_new_record(), this doesn't run
  its own synchronous retry loop (see the plan's clarification #4).

Every attempt persists one ProvisioningAttempt row, same audit-trail
principle as WebhookDelivery."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from tidybridge.config import settings
from tidybridge.models import ClientRecord, ProvisioningAttempt, ProvisioningJob

TIMEOUT_SECONDS = 5.0

_INDEXED_SEGMENT = re.compile(r"^(\w+)\[(\d+)\]$")


def _load_mapping() -> dict[str, str]:
    with open(settings.provisioning_mapping_path) as f:
        config = yaml.safe_load(f)
    return config["mapping"]


def _step(cursor: dict, part: str) -> dict:
    """Descends one path segment into a nested dict/list structure being
    built up, creating containers as needed - "emails[0]" creates/grows
    a list and returns its dict at that index, a plain key creates/
    returns a nested dict."""
    match = _INDEXED_SEGMENT.match(part)
    if not match:
        return cursor.setdefault(part, {})
    key, index = match.group(1), int(match.group(2))
    items = cursor.setdefault(key, [])
    while len(items) <= index:
        items.append({})
    return items[index]


def _assign(cursor: dict, part: str, value: object) -> None:
    match = _INDEXED_SEGMENT.match(part)
    if not match:
        cursor[part] = value
        return
    key, index = match.group(1), int(match.group(2))
    items = cursor.setdefault(key, [])
    while len(items) <= index:
        items.append({})
    items[index] = value


def _set_path(body: dict, path: str, value: object) -> None:
    """Sets a possibly-nested key from a dotted/bracketed path like
    "name.givenName" or "emails[0].value" - just enough for the SCIM
    shapes this project's mapping actually uses, not a general JSONPath
    implementation.
    ponytail: no support for paths deeper than one bracket segment;
    extend _INDEXED_SEGMENT/_step if a future mapping ever needs one."""
    parts = path.split(".")
    cursor = body
    for part in parts[:-1]:
        cursor = _step(cursor, part)
    _assign(cursor, parts[-1], value)


def build_scim_payload(record: ClientRecord, mapping: dict[str, str]) -> dict:
    """Builds a SCIM-shaped POST /Users body per the configured field
    mapping. Each mapping value is a ClientRecord attribute name, looked
    up via getattr - except the literal strings "true"/"false" (quoted
    in the YAML so PyYAML doesn't coerce them to a real bool itself,
    which would make them indistinguishable from a field name), used as
    literal booleans instead. name.givenName/name.familyName split
    full_name on the first space - a documented limitation (see the
    spec): a one-word name repeats into both, and a middle name ends up
    entirely in familyName."""
    body: dict = {}
    for path, source in mapping.items():
        if source == "true":
            value: object = True
        elif source == "false":
            value = False
        elif path == "name.givenName":
            first, _, _rest = getattr(record, source).partition(" ")
            value = first
        elif path == "name.familyName":
            _first, _, rest = getattr(record, source).partition(" ")
            value = rest or _first
        else:
            value = getattr(record, source)
        _set_path(body, path, value)
    return body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provisioning.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tidybridge/provisioning.py tests/test_provisioning.py
git commit -m "feat: build SCIM-shaped payloads from a configured field mapping"
```

---

### Task 4: Enqueue and deliver a provisioning attempt

**Files:**
- Modify: `src/tidybridge/provisioning.py` (append)
- Modify: `tests/test_provisioning_worker.py` (append)

**Interfaces:**
- Consumes: `build_scim_payload`, `_load_mapping` (Task 3); `ProvisioningJob`, `ProvisioningAttempt` (Task 1).
- Produces: `enqueue_provisioning(db: Session, record: ClientRecord) -> ProvisioningJob | None` and `deliver_provisioning_attempt(db: Session, record: ClientRecord, attempt_number: int, idempotency_key: uuid.UUID) -> tuple[ProvisioningAttempt, str | None]` - the second element is the remote user id from a successful response body, `None` otherwise. Both consumed by Task 5's worker and Task 8's `ingest.py` wiring.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provisioning_worker.py`:

```python
from tidybridge.provisioning import deliver_provisioning_attempt, enqueue_provisioning


def test_enqueue_provisioning_creates_a_pending_job(db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()

    job = enqueue_provisioning(db, record)
    db.commit()

    assert job is not None
    assert job.status == "pending"
    assert job.record_id == record.id


def test_enqueue_provisioning_is_a_noop_without_a_configured_url(db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", None)
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    assert enqueue_provisioning(db, record) is None


def test_deliver_provisioning_attempt_records_a_connection_failure(db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module
    import uuid as uuid_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()

    attempt, remote_id = deliver_provisioning_attempt(db, record, 1, uuid_module.uuid4())

    assert attempt.success is False
    assert attempt.status_code is None
    assert attempt.error is not None
    assert remote_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provisioning_worker.py -v`
Expected: FAIL with `ImportError: cannot import name 'enqueue_provisioning'`

- [ ] **Step 3: Write the implementation**

Append to `src/tidybridge/provisioning.py`:

```python
def enqueue_provisioning(db: Session, record: ClientRecord) -> ProvisioningJob | None:
    """Called once per newly-inserted record, right after ingest (see
    ingest.py), same hook point as enqueue_delivery(). Just a fast DB
    insert - the actual HTTP attempt happens later, off the request
    path, in webhook_worker.py's process_due_provisioning_jobs()."""
    if not settings.provisioning_url:
        return None  # no target configured - nothing to do, not an error
    job = ProvisioningJob(record_id=record.id)
    db.add(job)
    db.flush()  # assigns job.id
    return job


def deliver_provisioning_attempt(
    db: Session, record: ClientRecord, attempt_number: int, idempotency_key: uuid.UUID
) -> tuple[ProvisioningAttempt, str | None]:
    """One HTTP attempt: builds the SCIM-shaped payload via the
    configured mapping, POSTs it, persists and commits exactly one
    ProvisioningAttempt row. Returns the attempt plus the remote user id
    the target system's response body carried (only set on a 2xx with
    an "id" field) - the caller (process_due_provisioning_jobs) needs
    that to fill in ProvisioningJob.remote_id, which doesn't fit
    ProvisioningAttempt's per-attempt audit shape (see the spec)."""
    mapping = _load_mapping()
    body = json.dumps(build_scim_payload(record, mapping)).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.provisioning_api_key:
        headers["Authorization"] = f"Bearer {settings.provisioning_api_key}"

    attempt = ProvisioningAttempt(
        record_id=record.id,
        url=settings.provisioning_url,
        success=False,
        attempt_number=attempt_number,
        idempotency_key=idempotency_key,
    )
    remote_id: str | None = None
    try:
        response = httpx.post(
            settings.provisioning_url, content=body, headers=headers, timeout=TIMEOUT_SECONDS
        )
        attempt.status_code = response.status_code
        # 409 (duplicate userName) means the user already exists on the
        # target system - resolved, not a failure to retry (see the
        # spec's "skipped_exists is terminal" rationale).
        attempt.success = response.is_success or response.status_code == 409
        if response.is_success:
            try:
                remote_id = response.json().get("id")
            except ValueError:
                remote_id = None  # non-JSON 2xx body - nothing to capture
        elif response.status_code != 409:
            attempt.error = f"non-2xx response: {response.status_code}"
    except httpx.HTTPError as exc:
        attempt.error = f"{type(exc).__name__}: {exc}"

    db.add(attempt)
    db.commit()
    return attempt, remote_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provisioning_worker.py tests/test_provisioning.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tidybridge/provisioning.py tests/test_provisioning_worker.py
git commit -m "feat: enqueue and deliver provisioning attempts"
```

---

### Task 5: Worker - claim and process due provisioning jobs

**Files:**
- Modify: `src/tidybridge/webhook_worker.py` (append)
- Modify: `scripts/webhook_worker.py`
- Modify: `tests/test_provisioning_worker.py` (append)

**Interfaces:**
- Consumes: `deliver_provisioning_attempt` (Task 4), `_backoff_seconds` (`tidybridge.webhooks`, existing, shared - not duplicated), `ProvisioningJob` (Task 1).
- Produces: `process_due_provisioning_jobs(db: Session, limit: int = 20) -> int`, same contract as `process_due_jobs`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provisioning_worker.py`:

```python
import time
from datetime import UTC, datetime

from tidybridge.webhook_worker import process_due_provisioning_jobs


def test_process_due_provisioning_jobs_marks_a_job_dead_after_max_attempts(db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    monkeypatch.setattr(provisioning_module.settings, "webhook_max_attempts", 2)
    monkeypatch.setattr(provisioning_module.settings, "webhook_retry_backoff_seconds", 0.01)

    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()
    job = enqueue_provisioning(db, record)
    db.commit()
    job_id = job.id

    process_due_provisioning_jobs(db)  # attempt 1 fails -> scheduled for attempt 2
    time.sleep(0.05)
    process_due_provisioning_jobs(db)  # attempt 2 fails -> max_attempts reached -> dead

    refreshed = db.get(ProvisioningJob, job_id)
    assert refreshed.status == "dead"
    assert refreshed.attempt_number == 2


def test_process_due_provisioning_jobs_treats_a_409_as_skipped_exists(
    client: TestClient, db: Session, monkeypatch
):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _ConflictHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(409)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _ConflictHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", f"http://127.0.0.1:{port}/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()
    job = enqueue_provisioning(db, record)
    db.commit()
    job_id = job.id

    process_due_provisioning_jobs(db)
    server.shutdown()

    refreshed = db.get(ProvisioningJob, job_id)
    assert refreshed.status == "skipped_exists"  # terminal, not retried
    assert refreshed.attempt_number == 1


def test_process_due_provisioning_jobs_succeeds_and_captures_remote_id(db: Session, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _CreatedHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            body = b'{"id": "usr_8f3a"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _CreatedHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", f"http://127.0.0.1:{port}/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()
    job = enqueue_provisioning(db, record)
    db.commit()
    job_id = job.id

    process_due_provisioning_jobs(db)
    server.shutdown()

    refreshed = db.get(ProvisioningJob, job_id)
    assert refreshed.status == "done"
    assert refreshed.remote_id == "usr_8f3a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provisioning_worker.py -v`
Expected: FAIL with `ImportError: cannot import name 'process_due_provisioning_jobs'`

- [ ] **Step 3: Write the implementation**

Append to `src/tidybridge/webhook_worker.py` (add `ProvisioningJob` to the existing `from tidybridge.models import ...` line, and `deliver_provisioning_attempt` alongside the existing `webhooks` import):

```python
from tidybridge.models import ClientRecord, ProvisioningJob, WebhookJob
from tidybridge.provisioning import deliver_provisioning_attempt
from tidybridge.webhooks import _backoff_seconds, deliver_attempt
```

Then append the new function:

```python
def process_due_provisioning_jobs(db: Session, limit: int = 20) -> int:
    """Same claim pattern as process_due_jobs() above, for the
    provisioning_jobs queue (provisioning.py's enqueue_provisioning()
    writes to it) - see that function's docstring for why one job at a
    time. One difference from webhook delivery: a 409 (the user already
    exists on the target system) is terminal success-equivalent
    ("skipped_exists"), not a failure to retry - see the spec."""
    processed = 0
    for _ in range(limit):
        job = db.execute(
            select(ProvisioningJob)
            .where(
                ProvisioningJob.status == "pending",
                ProvisioningJob.available_at <= datetime.now(UTC),
            )
            .order_by(ProvisioningJob.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            break

        record = db.get(ClientRecord, job.record_id)
        attempt, remote_id = deliver_provisioning_attempt(
            db, record, job.attempt_number, job.idempotency_key
        )
        if attempt.status_code == 409:
            job.status = "skipped_exists"
        elif attempt.success:
            job.status = "done"
            job.remote_id = remote_id
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

In `scripts/webhook_worker.py`, wire it into the poll loop:

```python
from tidybridge.webhook_worker import process_due_jobs, process_due_provisioning_jobs
```

```python
        db = SessionLocal()
        try:
            processed = process_due_jobs(db) + process_due_provisioning_jobs(db)
            if processed:
                print(f"processed {processed} job(s)")
        finally:
            db.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provisioning_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tidybridge/webhook_worker.py scripts/webhook_worker.py tests/test_provisioning_worker.py
git commit -m "feat: worker claims and processes due provisioning jobs"
```

---

### Task 6: Fire provisioning automatically at ingest time

**Files:**
- Modify: `src/tidybridge/ingest.py:29` (import) and `:124-125` (enqueue loop)
- Test: `tests/test_provisioning.py` (append)

**Interfaces:**
- Consumes: `enqueue_provisioning` (Task 4).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provisioning.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tidybridge.models import ProvisioningJob


def _upload_single_row(client: TestClient):
    csv_body = (
        "Customer,Contact Email,Order Date,Order Total,Mobile Number\n"
        "Ada Lovelace,ada@shop.com,2026-09-01,100.00,+34 600 00 00 00\n"
    )
    return client.post(
        "/records/upload", files={"file": ("single.csv", csv_body.encode(), "text/csv")}
    )


def test_upload_enqueues_a_provisioning_job_when_a_url_is_configured(
    client: TestClient, db: Session, monkeypatch
):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    job = db.execute(
        select(ProvisioningJob).where(ProvisioningJob.record_id == record_id)
    ).scalar_one()
    assert job.status == "pending"


def test_upload_does_not_enqueue_provisioning_without_a_configured_url(
    client: TestClient, db: Session
):
    # PROVISIONING_URL is unset in tests (see conftest.py) - hermetic by default.
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    job = db.execute(
        select(ProvisioningJob).where(ProvisioningJob.record_id == record_id)
    ).scalar_one_or_none()
    assert job is None
```

(`select` is already imported in `test_provisioning.py` from Task 3? No - add `from sqlalchemy import select` to that file's imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provisioning.py -v -k upload`
Expected: FAIL - `test_upload_enqueues_a_provisioning_job_when_a_url_is_configured` finds no `ProvisioningJob` row (`scalar_one()` raises `NoResultFound`)

- [ ] **Step 3: Write the implementation**

In `src/tidybridge/ingest.py`, change the import line:

```python
from tidybridge.webhooks import enqueue_delivery
```

to:

```python
from tidybridge.provisioning import enqueue_provisioning
from tidybridge.webhooks import enqueue_delivery
```

And in the enqueue loop:

```python
    for record in inserted:
        enqueue_delivery(db, record)
```

to:

```python
    for record in inserted:
        enqueue_delivery(db, record)
        enqueue_provisioning(db, record)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provisioning.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tidybridge/ingest.py tests/test_provisioning.py
git commit -m "feat: enqueue provisioning automatically after ingest"
```

---

### Task 7: Add PROVISIONING_URL to the hermetic test default

**Files:**
- Modify: `tests/conftest.py:15`

**Interfaces:** none new - this just keeps the test suite hermetic the same way `WEBHOOK_URL` already is, so a developer's real `.env` (if it happens to set `PROVISIONING_URL`) can never leak into the test run.

- [ ] **Step 1: Add the env default**

```python
os.environ.setdefault("WEBHOOK_URL", "")  # no webhook during tests - keep them hermetic
os.environ.setdefault("PROVISIONING_URL", "")  # same - no provisioning target during tests
```

- [ ] **Step 2: Run the full suite to verify nothing broke**

Run: `pytest -v`
Expected: PASS (all prior tests plus the new provisioning ones)

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: keep PROVISIONING_URL unset by default in tests"
```

---

### Task 8: API endpoints

**Files:**
- Modify: `src/tidybridge/schemas.py` (append)
- Modify: `src/tidybridge/provisioning.py` (append `replay_provisioning`)
- Modify: `src/tidybridge/main.py` (imports + 3 new endpoints, placed after `replay_webhook`, before `delete_record`)
- Test: `tests/test_provisioning.py` (append)

**Interfaces:**
- Produces: `ProvisioningAttemptOut`, `ProvisioningJobStatusOut` (Pydantic, `tidybridge.schemas`); `replay_provisioning(db: Session, record: ClientRecord) -> ProvisioningJob` (`tidybridge.provisioning`); endpoints `GET /records/{id}/provisioning-status`, `GET /records/{id}/provisioning`, `POST /records/{id}/provisioning/replay` - consumed by Task 9's frontend.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_provisioning.py`:

```python
def test_provisioning_status_is_not_configured_without_a_url(client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = client.get(f"/records/{record_id}/provisioning-status")
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


def test_provisioning_status_is_pending_before_the_worker_runs(client: TestClient, monkeypatch):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    response = client.get(f"/records/{record_id}/provisioning-status")
    assert response.json()["status"] == "pending"
    assert response.json()["attempt_number"] == 1
    assert response.json()["remote_id"] is None


def test_provisioning_status_respects_ownership(client: TestClient, other_client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = other_client.get(f"/records/{record_id}/provisioning-status")
    assert response.status_code == 404


def test_get_provisioning_returns_the_attempt_history(client: TestClient, db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module
    from tidybridge.webhook_worker import process_due_provisioning_jobs

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    process_due_provisioning_jobs(db)

    attempts = client.get(f"/records/{record_id}/provisioning").json()
    assert len(attempts) == 1
    assert attempts[0]["success"] is False
    assert attempts[0]["record_id"] == record_id


def test_replay_provisioning_resets_the_job_and_requires_a_configured_url(
    client: TestClient, monkeypatch
):
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    no_url_response = client.post(f"/records/{record_id}/provisioning/replay")
    assert no_url_response.status_code == 400

    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    response = client.post(f"/records/{record_id}/provisioning/replay")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["attempt_number"] == 1


def test_replay_provisioning_respects_ownership(client: TestClient, other_client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = other_client.post(f"/records/{record_id}/provisioning/replay")
    assert response.status_code == 404


def test_provisioning_endpoints_require_auth():
    from tidybridge.main import app

    unauthenticated = TestClient(app)
    assert unauthenticated.get("/records/00000000-0000-0000-0000-000000000000/provisioning-status").status_code == 401
    assert unauthenticated.get("/records/00000000-0000-0000-0000-000000000000/provisioning").status_code == 401
    assert unauthenticated.post("/records/00000000-0000-0000-0000-000000000000/provisioning/replay").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provisioning.py -v -k "provisioning_status or get_provisioning or replay_provisioning or endpoints_require_auth"`
Expected: FAIL with 404s (routes don't exist yet)

- [ ] **Step 3: Write the implementation**

Append to `src/tidybridge/schemas.py`:

```python
class ProvisioningAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    record_id: uuid.UUID
    url: str
    status_code: int | None
    success: bool
    error: str | None
    attempt_number: int
    idempotency_key: uuid.UUID
    attempted_at: datetime


class ProvisioningJobStatusOut(BaseModel):
    """The provisioning pipeline's current state for one record - same
    shape/purpose as WebhookJobStatusOut, plus remote_id once the target
    system has actually created the user. "not_configured" when no
    PROVISIONING_URL is set at all. Doesn't reflect a replay's own
    outcome synchronously - replay_provisioning only resets the job for
    the worker to pick up (see the plan's clarification #4)."""

    status: str  # "pending" | "done" | "skipped_exists" | "dead" | "not_configured"
    attempt_number: int | None
    available_at: datetime | None
    remote_id: str | None
```

Append to `src/tidybridge/provisioning.py`:

```python
def replay_provisioning(db: Session, record: ClientRecord) -> ProvisioningJob:
    """Manually enqueues a fresh provisioning attempt for one record -
    real, separate action from the automatic queue, for the same reason
    replay_webhook exists (main.py). Unlike replay_webhook (which runs
    its own retry loop synchronously and returns the resulting
    delivery), this just resets/creates the ProvisioningJob row and lets
    the worker pick it up, per the spec's "manually enqueue a fresh
    attempt" - nothing has actually run yet by the time this returns.
    Generates a fresh idempotency_key, same reasoning as
    notify_new_record()'s own replay key."""
    job = db.execute(
        select(ProvisioningJob).where(ProvisioningJob.record_id == record.id)
    ).scalar_one_or_none()
    if job is None:
        job = ProvisioningJob(record_id=record.id)
        db.add(job)
    else:
        job.status = "pending"
        job.attempt_number = 1
        job.available_at = datetime.now(UTC)
        job.idempotency_key = uuid.uuid4()
        job.remote_id = None
    db.commit()
    db.refresh(job)
    return job
```

In `src/tidybridge/main.py`, update the models/schemas imports:

```python
from tidybridge.models import ClientRecord, IngestionRun, WebhookDelivery, WebhookJob
```

to:

```python
from tidybridge.models import (
    ClientRecord,
    IngestionRun,
    ProvisioningAttempt,
    ProvisioningJob,
    WebhookDelivery,
    WebhookJob,
)
```

```python
from tidybridge.schemas import (
    ClientRecordOut,
    IngestionRunOut,
    IngestionRunsPage,
    IngestResult,
    RecordsPage,
    WebhookDeliveryOut,
    WebhookJobStatusOut,
)
```

to:

```python
from tidybridge.schemas import (
    ClientRecordOut,
    IngestionRunOut,
    IngestionRunsPage,
    IngestResult,
    ProvisioningAttemptOut,
    ProvisioningJobStatusOut,
    RecordsPage,
    WebhookDeliveryOut,
    WebhookJobStatusOut,
)
```

```python
from tidybridge.webhooks import notify_new_record
```

to:

```python
from tidybridge.provisioning import replay_provisioning
from tidybridge.webhooks import notify_new_record
```

Then add the three endpoints right after `replay_webhook` (before `delete_record`):

```python
@app.get("/records/{record_id}/provisioning-status", response_model=ProvisioningJobStatusOut)
def get_record_provisioning_status(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> ProvisioningJobStatusOut:
    """The automatic post-ingest provisioning pipeline's current state
    for one record - same shape/purpose as get_record_webhook_status
    above."""
    _get_owned_record(db, record_id, user)
    job = db.execute(
        select(ProvisioningJob).where(ProvisioningJob.record_id == record_id)
    ).scalar_one_or_none()
    if job is None:
        return ProvisioningJobStatusOut(
            status="not_configured", attempt_number=None, available_at=None, remote_id=None
        )
    return ProvisioningJobStatusOut(
        status=job.status,
        attempt_number=job.attempt_number,
        available_at=job.available_at,
        remote_id=job.remote_id,
    )


@app.get("/records/{record_id}/provisioning", response_model=list[ProvisioningAttemptOut])
def get_record_provisioning(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> list:
    _get_owned_record(db, record_id, user)
    query = (
        select(ProvisioningAttempt)
        .where(ProvisioningAttempt.record_id == record_id)
        .order_by(ProvisioningAttempt.attempted_at, ProvisioningAttempt.id)
    )
    return db.execute(query).scalars().all()


@app.post("/records/{record_id}/provisioning/replay", response_model=ProvisioningJobStatusOut)
def replay_provisioning_endpoint(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> ProvisioningJobStatusOut:
    """Manually re-enqueues provisioning for one record, on demand - see
    replay_provisioning()'s docstring in provisioning.py for how this
    differs from replay_webhook above."""
    record = _get_owned_record(db, record_id, user)
    if not settings.provisioning_url:
        raise HTTPException(status_code=400, detail="No provisioning URL is configured")
    job = replay_provisioning(db, record)
    return ProvisioningJobStatusOut(
        status=job.status,
        attempt_number=job.attempt_number,
        available_at=job.available_at,
        remote_id=job.remote_id,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_provisioning.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `pytest -v`
Expected: PASS (no regressions in existing webhook/export/record tests)

- [ ] **Step 6: Commit**

```bash
git add src/tidybridge/schemas.py src/tidybridge/provisioning.py src/tidybridge/main.py tests/test_provisioning.py
git commit -m "feat: add provisioning status/history/replay endpoints"
```

---

### Task 9: Frontend - Provisioning section on RecordDetailPage

**Files:**
- Modify: `frontend/src/api/types.ts` (append)
- Modify: `frontend/src/api/client.ts` (imports + append)
- Modify: `frontend/src/pages/RecordDetailPage.tsx`

**Interfaces:**
- Consumes: `GET /records/{id}/provisioning-status`, `GET /records/{id}/provisioning`, `POST /records/{id}/provisioning/replay` (Task 8).

This task is UI wiring with no backend logic to unit-test; verified manually (Step 5) rather than via a new automated test, matching this project's existing frontend testing posture (no frontend test suite exists yet - `RecordDetailPage.tsx`'s webhook section has none either).

- [ ] **Step 1: Add the TypeScript types**

Append to `frontend/src/api/types.ts`:

```typescript
export interface ProvisioningAttempt {
  id: number;
  record_id: string;
  url: string;
  status_code: number | null;
  success: boolean;
  error: string | null;
  attempt_number: number;
  attempted_at: string;
}

// The provisioning pipeline's current state for one record - same
// shape/purpose as WebhookJobStatus above, plus remote_id once the
// target system has actually created the user. Doesn't reflect a
// replay's own outcome synchronously - see the backend's
// replay_provisioning docstring.
export interface ProvisioningJobStatus {
  status: "pending" | "done" | "skipped_exists" | "dead" | "not_configured";
  attempt_number: number | null;
  available_at: string | null;
  remote_id: string | null;
}
```

- [ ] **Step 2: Add the API client functions**

In `frontend/src/api/client.ts`, update the type import:

```typescript
import type {
  ClientRecord,
  CurrentUser,
  IngestionRun,
  IngestionRunsPage,
  IngestResult,
  RecordsPage,
  WebhookDelivery,
  WebhookJobStatus,
} from "./types";
```

to:

```typescript
import type {
  ClientRecord,
  CurrentUser,
  IngestionRun,
  IngestionRunsPage,
  IngestResult,
  ProvisioningAttempt,
  ProvisioningJobStatus,
  RecordsPage,
  WebhookDelivery,
  WebhookJobStatus,
} from "./types";
```

Append after `replayWebhook`:

```typescript
export async function getRecordProvisioning(id: string): Promise<ProvisioningAttempt[]> {
  return request<ProvisioningAttempt[]>(`/records/${id}/provisioning`);
}

export async function getRecordProvisioningStatus(id: string): Promise<ProvisioningJobStatus> {
  return request<ProvisioningJobStatus>(`/records/${id}/provisioning-status`);
}

// Unlike replayWebhook, this doesn't resolve with a delivery result -
// the backend only resets the job for its worker to pick up later (see
// replay_provisioning's docstring). Callers refetch status/history
// after a short delay the same way handleReplayProvisioning does.
export async function replayProvisioning(id: string): Promise<ProvisioningJobStatus> {
  return request<ProvisioningJobStatus>(`/records/${id}/provisioning/replay`, { method: "POST" });
}
```

- [ ] **Step 3: Wire up RecordDetailPage.tsx**

Update the type import:

```typescript
import type { ClientRecord, WebhookDelivery, WebhookJobStatus } from "../api/types";
```

to:

```typescript
import type {
  ClientRecord,
  ProvisioningAttempt,
  ProvisioningJobStatus,
  WebhookDelivery,
  WebhookJobStatus,
} from "../api/types";
```

Add state (after the existing `webhookStatus` line):

```typescript
  const [provisioning, setProvisioning] = useState<ProvisioningAttempt[]>([]);
  const [provisioningStatus, setProvisioningStatus] = useState<ProvisioningJobStatus | null>(null);
```

Add replay state (after `replayError`):

```typescript
  const [replayingProvisioning, setReplayingProvisioning] = useState(false);
  const [provisioningReplayError, setProvisioningReplayError] = useState<string | null>(null);
```

Update the load effect:

```typescript
    Promise.all([api.getRecord(id), api.getRecordWebhooks(id), api.getRecordWebhookStatus(id)])
      .then(([recordResult, webhooksResult, statusResult]) => {
        setRecord(recordResult);
        setWebhooks(webhooksResult);
        setWebhookStatus(statusResult);
      })
```

to:

```typescript
    Promise.all([
      api.getRecord(id),
      api.getRecordWebhooks(id),
      api.getRecordWebhookStatus(id),
      api.getRecordProvisioning(id),
      api.getRecordProvisioningStatus(id),
    ])
      .then(([recordResult, webhooksResult, statusResult, provisioningResult, provisioningStatusResult]) => {
        setRecord(recordResult);
        setWebhooks(webhooksResult);
        setWebhookStatus(statusResult);
        setProvisioning(provisioningResult);
        setProvisioningStatus(provisioningStatusResult);
      })
```

Add a replay handler (after `handleReplay`):

```typescript
  async function handleReplayProvisioning() {
    if (!id) return;
    setReplayingProvisioning(true);
    setProvisioningReplayError(null);
    try {
      const status = await api.replayProvisioning(id);
      setProvisioningStatus(status); // freshly-reset job state - the worker hasn't run yet
    } catch (err) {
      setProvisioningReplayError(
        err instanceof ApiError ? err.message : "Couldn't retry provisioning.",
      );
    } finally {
      setReplayingProvisioning(false);
    }
  }
```

Add the new section (after the closing `</div>` of "Webhook deliveries", before `<ConfirmDialog`):

```tsx
      <div className="mt-8">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Provisioning</h2>
            <ProvisioningStatusBadge status={provisioningStatus} />
          </div>
          <button
            type="button"
            onClick={handleReplayProvisioning}
            disabled={replayingProvisioning}
            className="rounded-md border border-border px-3 py-1 text-xs hover:bg-secondary transition disabled:opacity-50"
          >
            {replayingProvisioning ? "Retrying…" : "Retry provisioning"}
          </button>
        </div>
        {provisioningReplayError && (
          <p className="mb-2 text-sm text-red-600">{provisioningReplayError}</p>
        )}
        {provisioningStatus?.remote_id && (
          <p className="mb-2 text-sm text-muted-foreground">
            Provisioned as <code className="text-foreground">{provisioningStatus.remote_id}</code> on
            the target system.
          </p>
        )}
        {provisioning.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No provisioning target was configured, or none has been attempted for this record.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <thead className="bg-secondary text-left text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">Attempt</th>
                  <th className="px-4 py-2 font-medium">Attempted</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {provisioning.map((p) => (
                  <tr key={p.id} className="border-t border-border">
                    <td className="px-4 py-2 text-muted-foreground">#{p.attempt_number}</td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {new Date(p.attempted_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-2">{p.status_code ?? "—"}</td>
                    <td className="px-4 py-2">
                      {p.success ? (
                        <span className="text-primary">
                          {p.status_code === 409 ? "Already existed" : "Provisioned"}
                        </span>
                      ) : (
                        <span className="text-red-600" title={p.error ?? undefined}>
                          Failed
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
```

Add the badge component (after `WebhookStatusBadge`):

```tsx
// Same shape as WebhookStatusBadge, plus "skipped_exists" - a 409 (the
// user already exists on the target system) is terminal
// success-equivalent, not a failure (see the backend spec).
function ProvisioningStatusBadge({ status }: { status: ProvisioningJobStatus | null }) {
  if (!status || status.status === "not_configured") return null;

  if (status.status === "dead") {
    return (
      <span className="rounded-full bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400 px-2 py-0.5 text-xs">
        Provisioning failed permanently
      </span>
    );
  }
  if (status.status === "done" || status.status === "skipped_exists") {
    return (
      <span className="rounded-full bg-accent text-accent-foreground px-2 py-0.5 text-xs">
        {status.status === "done" ? "Provisioned" : "Already existed"}
      </span>
    );
  }
  // "pending"
  return (
    <span className="rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400 px-2 py-0.5 text-xs">
      {status.attempt_number && status.attempt_number > 1
        ? `Retrying (attempt ${status.attempt_number})`
        : "Provisioning pending"}
    </span>
  );
}
```

- [ ] **Step 4: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Manually verify in the browser**

Run the backend with `PROVISIONING_URL`/`PROVISIONING_API_KEY` pointed at a throwaway local HTTP server (e.g. `python3 -m http.server`, which 501s any POST - enough to see a "Failed" attempt row), upload a CSV, open the record's detail page, confirm the "Provisioning" section renders, the badge updates after clicking "Retry provisioning" and reloading, and `remote_id` renders once a real 2xx response with an `id` field is returned.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/pages/RecordDetailPage.tsx
git commit -m "feat: show provisioning status and history on the record detail page"
```

---

## Self-Review

**Spec coverage:**
- Data model (`ProvisioningJob`/`ProvisioningAttempt`, both columns lists) → Task 1.
- Enqueue flow, gated by `provisioning_url` → Task 4, wired in Task 6.
- Worker flow (claim pattern, 2xx/409/other branching, shared backoff helper) → Task 5.
- API endpoints (status/history/replay, ownership, routing) → Task 8.
- Frontend section → Task 9.
- Config (YAML mapping + two settings) → Task 2 (with clarification #2 on the templating question).
- Testing strategy (enqueue, worker success/conflict/failure/dead, endpoints, field-mapping unit test) → Tasks 1, 3, 4, 5, 6, 8.
- Out-of-scope items were not implemented anywhere in this plan (checked against each of Tasks 1-9).

**Placeholder scan:** No "TBD"/"similar to Task N" - every step above has literal, runnable code. `deliver_provisioning_attempt`'s `response.json()` failure path is handled explicitly (`except ValueError`), not glossed over.

**Type consistency:** `ProvisioningJob`/`ProvisioningAttempt` (Task 1) → imported identically in Tasks 4, 5, 8. `deliver_provisioning_attempt`'s `tuple[ProvisioningAttempt, str | None]` return (Task 4) is unpacked the same way in Task 5's worker. `ProvisioningJobStatusOut`/`ProvisioningAttemptOut` field names (Task 8) match `ProvisioningJobStatus`/`ProvisioningAttempt` TS interfaces (Task 9) exactly. `replay_provisioning`'s return type (`ProvisioningJob`) matches how Task 8's endpoint reads `job.status`/`job.attempt_number`/`job.available_at`/`job.remote_id`.
