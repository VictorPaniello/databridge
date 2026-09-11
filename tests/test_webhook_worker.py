"""Background webhook-delivery queue: WebhookJob rows enqueue_delivery()
(webhooks.py) creates and process_due_jobs() (webhook_worker.py) claims
and works through - see webhook_worker.py's module docstring for why
this exists instead of delivering inline during POST /records/upload."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from databridge.models import ClientRecord, WebhookJob


def test_webhook_job_can_be_created_with_expected_defaults(db: Session):
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    job = WebhookJob(record_id=record.id)
    db.add(job)
    db.commit()

    fetched = db.execute(select(WebhookJob).where(WebhookJob.record_id == record.id)).scalar_one()
    assert fetched.status == "pending"
    assert fetched.attempt_number == 1
    assert fetched.available_at is not None
    assert fetched.created_at is not None
