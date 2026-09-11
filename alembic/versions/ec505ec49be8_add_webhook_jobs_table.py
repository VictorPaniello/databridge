"""add webhook_jobs table

Revision ID: ec505ec49be8
Revises: e986a7123298
Create Date: 2026-09-11 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ec505ec49be8"
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
