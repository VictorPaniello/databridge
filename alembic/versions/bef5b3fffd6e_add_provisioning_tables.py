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
