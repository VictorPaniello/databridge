"""Database tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from databridge.db import Base


class IngestionRun(Base):
    """One row per upload - the persisted, queryable summary of what an
    ingest actually did, not just what the HTTP response said at the
    moment it happened. Before this, IngestResult (schemas.py) was the
    *only* record of a run's outcome - visible in the response body and
    nowhere else, gone the moment that response was read (or missed:
    closed tab, a script that didn't log it, a client asking three days
    later "did my 50,000-row file actually finish?"). Never mutated after
    creation except to fill in rows_clean/rows_flagged once the row loop
    that computes them finishes (see ingest.py) - an audit record, not a
    live-updating one."""

    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), nullable=False, index=True
    )
    """ondelete="cascade": deleting a User (DELETE /users/me - the account
    self-erasure endpoint, main.py) removes every ingestion run they own
    along with it, rather than leaving orphaned rows or failing the
    deletion outright on the FK."""
    source_file: Mapped[str] = mapped_column(String, nullable=False)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_clean: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_flagged: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_dropped_duplicates: Mapped[int] = mapped_column(Integer, nullable=False)
    """Rows removed by tidycsv's own within-file dedup (two rows in the
    *same upload* sharing a key column) - see flag_duplicates in
    ingest.py. Distinct from rows_skipped_existing below: this is about
    the file's own internal duplicates, not about what was already in
    the database before this upload started."""
    rows_skipped_existing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Rows that matched an email already ingested for this owner in a
    *previous* upload - re-uploading the same client list is a no-op,
    not an error (see ingest.py), but those skipped rows still need to
    be accounted for somewhere, or rows_total stops summing to
    rows_clean + rows_flagged + rows_dropped_duplicates + this field,
    which is exactly the gap this field exists to close - found while
    writing this feature's own tests, not assumed correct."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    records: Mapped[list[ClientRecord]] = relationship(back_populates="ingestion_run")


class ClientRecord(Base):
    __tablename__ = "client_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), nullable=True, index=True
    )
    """Which engineer this client record belongs to - the basis for each
    engineer only seeing their own clients. Nullable because records
    ingested before authentication existed have no owner; a record with no
    owner is visible to nobody rather than to everybody, which is the safer
    failure direction for client data. ondelete="cascade": deleting a User
    (DELETE /users/me) removes every client record they own along with
    it - real, full erasure of an account and everything tied to it, not
    just the account row itself."""
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="cascade"),
        nullable=True,
        index=True,
    )
    """Which upload created this record - lets a caller go from "this run
    had 3 flagged rows" (IngestionRun) to "show me exactly those rows"
    (GET /records?ingestion_run_id=...) instead of only having per-record
    has_issues/issues with no way to group them by the upload that
    produced them. Nullable for the same reason owner_id is: every record
    that predates this column has no run to point at. ondelete="cascade"
    so a record can never outlive the run that created it, whichever of
    the two cascade paths (this one, or its own owner_id above) a User
    deletion happens to take first."""
    ingestion_run: Mapped[IngestionRun | None] = relationship(back_populates="records")
    source_file: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    signup_date: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    has_issues: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    issues: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    webhook_deliveries: Mapped[list[WebhookDelivery]] = relationship(
        back_populates="record", passive_deletes=True
    )
    """passive_deletes=True: lets the database's own ON DELETE CASCADE (see
    WebhookDelivery.record_id) do the cleanup instead of SQLAlchemy
    SELECTing every delivery first to delete them one by one - matters for
    DELETE /records/{id} (the GDPR erasure endpoint), where deleting a
    client record must actually remove its delivery audit trail too, not
    leave orphaned rows or fail on the foreign key."""


class WebhookDelivery(Base):
    """Audit log: every attempt to notify an external system about a new
    record, whether it succeeded or not. A production integration should
    never let a failed notification vanish silently."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_records.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    """1 for the first try, 2+ for each retry after it (see webhooks.py -
    up to Settings.webhook_max_attempts total). One row per attempt, not
    one row overwritten in place, so the audit trail shows the full
    retry history for a record, not just the final outcome."""
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    record: Mapped[ClientRecord] = relationship(back_populates="webhook_deliveries")


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
