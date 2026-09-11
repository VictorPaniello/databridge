"""Pydantic schemas: the shape of data crossing the API boundary. Kept
separate from the SQLAlchemy models in models.py - those describe storage,
these describe what the API actually exposes."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClientRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ingestion_run_id: uuid.UUID | None
    source_file: str
    full_name: str | None
    email: str | None
    signup_date: str | None
    amount: str | None
    phone: str | None
    has_issues: bool
    issues: list[dict] | None
    created_at: datetime


class WebhookDeliveryOut(BaseModel):
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


class IngestResult(BaseModel):
    ingestion_run_id: uuid.UUID
    rows_total: int
    rows_clean: int
    rows_flagged: int
    rows_dropped_duplicates: int
    # rows_total == rows_clean + rows_flagged + rows_dropped_duplicates +
    # rows_skipped_existing always holds - every row is accounted for as
    # exactly one of these four, never silently unaccounted.
    rows_skipped_existing: int
    records: list[ClientRecordOut]


class IngestionRunOut(BaseModel):
    """The persisted counterpart of IngestResult - what GET /ingestion-runs
    (and /ingestion-runs/{id}) return. Same stat fields as IngestResult,
    plus what IngestResult never carried: when it happened and which file
    it was, so a run is still findable after the response that first
    reported it is long gone."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_file: str
    rows_total: int
    rows_clean: int
    rows_flagged: int
    rows_dropped_duplicates: int
    rows_skipped_existing: int
    created_at: datetime


class IngestionRunsPage(BaseModel):
    items: list[IngestionRunOut]
    total: int
    limit: int
    offset: int


class RecordsPage(BaseModel):
    """GET /records used to return a bare `list[ClientRecordOut]` - every
    matching row, in one response, no matter how many. Fine for a demo
    account with a handful of rows, a real problem the moment a client
    upload puts tens of thousands of records behind one engineer: one
    unbounded query, one unbounded JSON body, no way to know how many
    more there are. `items` is capped per request (`limit`, enforced
    server-side - see main.py's Query bounds); `total` is the real
    count across every page, not just this one, so a caller (or the
    frontend) knows when it has fetched everything."""

    items: list[ClientRecordOut]
    total: int
    limit: int
    offset: int
