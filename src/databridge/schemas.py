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
    attempted_at: datetime


class IngestResult(BaseModel):
    rows_total: int
    rows_clean: int
    rows_flagged: int
    rows_dropped_duplicates: int
    records: list[ClientRecordOut]
