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
