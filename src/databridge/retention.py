"""Client data retention: deletes IngestionRun and ClientRecord rows
older than settings.client_data_retention_days - the real, automated
enforcement behind the Privacy Policy's "data is kept for up to a year"
promise for data an engineer uploads about their own clients.
Deliberately doesn't touch an engineer's own account (email/name/phone)
or password - that's kept until they delete it themselves
(DELETE /users/me, main.py); this only ages out the client data a
stale/abandoned account has been sitting on.

The actual entrypoint is scripts/retention_sweep.py, which just calls
run() below - the same pattern as backup.py/scripts/backup_db.py: logic
lives in the installed package so it can be imported and tested the same
way as everything else in this project, not only exercisable by running
the standalone script."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from databridge.config import settings
from databridge.db import SessionLocal
from databridge.models import ClientRecord, IngestionRun


def sweep_expired_client_data(db: Session) -> dict[str, int]:
    """Deletes every IngestionRun older than the retention window in one
    statement - ON DELETE CASCADE (see models.py's ClientRecord/
    WebhookDelivery foreign keys) takes its ClientRecords and their
    WebhookDeliveries with it as part of the same database operation, not
    a separate per-table pass here. Also deletes any ClientRecord past
    the window that has no ingestion_run_id at all (predates that column
    - see its own migration - so there's no run for it to cascade from):
    belt-and-suspenders, so a record's age is what decides its fate
    regardless of when the run-linking feature shipped relative to it.

    Returns counts rather than just logging, so a caller (the real
    scheduled job, or a test) can assert on what actually happened - a
    retention sweep that silently deletes things is exactly the kind of
    "trust me" behavior a data-deletion feature should never be."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.client_data_retention_days)

    deleted_runs = db.execute(delete(IngestionRun).where(IngestionRun.created_at < cutoff)).rowcount
    deleted_orphan_records = db.execute(
        delete(ClientRecord).where(
            ClientRecord.created_at < cutoff, ClientRecord.ingestion_run_id.is_(None)
        )
    ).rowcount
    db.commit()

    return {"deleted_runs": deleted_runs, "deleted_orphan_records": deleted_orphan_records}


def run() -> None:
    db = SessionLocal()
    try:
        result = sweep_expired_client_data(db)
        total = result["deleted_runs"] + result["deleted_orphan_records"]
        if total:
            print(
                f"Deleted {result['deleted_runs']} ingestion run(s) (cascading to their "
                f"records/webhook deliveries) and {result['deleted_orphan_records']} "
                f"orphaned record(s) older than {settings.client_data_retention_days} days."
            )
        else:
            print("No client data older than the retention window to delete.")
    finally:
        db.close()
