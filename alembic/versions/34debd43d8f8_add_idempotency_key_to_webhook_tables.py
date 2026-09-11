"""add idempotency_key to webhook tables

Revision ID: 34debd43d8f8
Revises: ec505ec49be8
Create Date: 2026-09-11 13:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "34debd43d8f8"
down_revision: Union[str, Sequence[str], None] = "ec505ec49be8"
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
