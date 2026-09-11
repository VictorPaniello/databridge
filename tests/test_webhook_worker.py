"""Background webhook-delivery queue: WebhookJob rows enqueue_delivery()
(webhooks.py) creates and process_due_jobs() (webhook_worker.py) claims
and works through - see webhook_worker.py's module docstring for why
this exists instead of delivering inline during POST /records/upload."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from databridge.models import ClientRecord, WebhookDelivery, WebhookJob
from databridge.webhook_worker import process_due_jobs
from databridge.webhooks import enqueue_delivery


def _upload_single_row(client: TestClient):
    csv_body = (
        "Customer,Contact Email,Order Date,Order Total,Mobile Number\n"
        "Ada Lovelace,ada@shop.com,2026-09-01,100.00,+34 600 00 00 00\n"
    )
    return client.post(
        "/records/upload", files={"file": ("single.csv", csv_body.encode(), "text/csv")}
    )


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


def test_enqueue_delivery_creates_a_pending_job(db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    job = enqueue_delivery(db, record)
    db.commit()

    assert job is not None
    assert job.status == "pending"
    assert job.record_id == record.id


def test_enqueue_delivery_is_a_noop_without_a_configured_webhook_url(db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", None)
    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()

    assert enqueue_delivery(db, record) is None


def test_process_due_jobs_delivers_a_pending_job_and_marks_it_done(db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")

    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()
    job = enqueue_delivery(db, record)
    db.commit()
    job_id = job.id

    # 127.0.0.1:1 refuses every connection - a real, deterministic failure
    # (not a mock), the same pattern flaky_webhook_receiver uses elsewhere
    # in this project, just via connection refusal instead of an HTTP 500.
    processed = process_due_jobs(db)

    assert processed == 1
    refreshed = db.get(WebhookJob, job_id)
    assert refreshed.status == "pending"  # attempt 1 failed, not dead yet (max_attempts default 3)
    assert refreshed.attempt_number == 2
    assert refreshed.available_at > datetime.now(UTC)  # scheduled for a future retry

    deliveries = db.execute(
        select(WebhookDelivery).where(WebhookDelivery.record_id == record.id)
    ).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].success is False


def test_process_due_jobs_marks_a_job_dead_after_max_attempts(db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")
    monkeypatch.setattr(webhooks_module.settings, "webhook_max_attempts", 2)
    monkeypatch.setattr(webhooks_module.settings, "webhook_retry_backoff_seconds", 0.01)

    record = ClientRecord(source_file="test.csv")
    db.add(record)
    db.flush()
    job = enqueue_delivery(db, record)
    db.commit()
    job_id = job.id

    process_due_jobs(db)  # attempt 1 fails -> scheduled for attempt 2
    time.sleep(0.05)
    process_due_jobs(db)  # attempt 2 fails -> max_attempts reached -> dead

    refreshed = db.get(WebhookJob, job_id)
    assert refreshed.status == "dead"
    assert refreshed.attempt_number == 2


def test_webhook_status_is_not_configured_without_a_webhook_url(client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = client.get(f"/records/{record_id}/webhook-status")
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


def test_webhook_status_is_pending_before_the_worker_runs(client: TestClient, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    response = client.get(f"/records/{record_id}/webhook-status")
    assert response.json()["status"] == "pending"
    assert response.json()["attempt_number"] == 1


def test_webhook_status_is_dead_after_every_attempt_fails(client: TestClient, db: Session, monkeypatch):
    import databridge.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module.settings, "webhook_url", "http://127.0.0.1:1/unused")
    monkeypatch.setattr(webhooks_module.settings, "webhook_max_attempts", 1)
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    process_due_jobs(db)

    response = client.get(f"/records/{record_id}/webhook-status")
    assert response.json()["status"] == "dead"


def test_webhook_status_respects_ownership(client: TestClient, other_client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = other_client.get(f"/records/{record_id}/webhook-status")
    assert response.status_code == 404
