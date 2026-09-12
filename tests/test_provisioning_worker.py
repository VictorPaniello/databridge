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
