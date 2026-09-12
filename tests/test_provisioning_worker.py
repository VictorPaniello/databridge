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
from tidybridge.provisioning import deliver_provisioning_attempt, enqueue_provisioning


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


def test_enqueue_provisioning_creates_a_pending_job(db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()

    job = enqueue_provisioning(db, record)
    db.commit()

    assert job is not None
    assert job.status == "pending"
    assert job.record_id == record.id


def test_enqueue_provisioning_is_a_noop_without_a_configured_url(db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", None)
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    assert enqueue_provisioning(db, record) is None


def test_deliver_provisioning_attempt_records_a_connection_failure(db: Session, monkeypatch):
    import uuid as uuid_module

    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()

    attempt, remote_id = deliver_provisioning_attempt(db, record, 1, uuid_module.uuid4())

    assert attempt.success is False
    assert attempt.status_code is None
    assert attempt.error is not None
    assert remote_id is None
