"""Database tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from databridge.db import Base


class ClientRecord(Base):
    __tablename__ = "client_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    """Which engineer this client record belongs to - the basis for each
    engineer only seeing their own clients. Nullable because records
    ingested before authentication existed have no owner; a record with no
    owner is visible to nobody rather than to everybody, which is the safer
    failure direction for client data."""
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
