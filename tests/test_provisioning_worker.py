"""Background provisioning queue: ProvisioningJob rows enqueue_provisioning()
(provisioning.py) creates and process_due_provisioning_jobs()
(webhook_worker.py) claims and works through - see webhook_worker.py's
module docstring for the shared claim pattern, and the spec's "409 is
terminal success" rationale for why this isn't just a copy of the
webhook queue."""

from __future__ import annotations

import time

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


def test_process_due_provisioning_jobs_marks_a_job_dead_after_max_attempts(db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module
    from tidybridge.webhook_worker import process_due_provisioning_jobs

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    monkeypatch.setattr(provisioning_module.settings, "webhook_max_attempts", 2)
    monkeypatch.setattr(provisioning_module.settings, "webhook_retry_backoff_seconds", 0.01)

    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()
    job = enqueue_provisioning(db, record)
    db.commit()
    job_id = job.id

    process_due_provisioning_jobs(db)  # attempt 1 fails -> scheduled for attempt 2
    time.sleep(0.05)
    process_due_provisioning_jobs(db)  # attempt 2 fails -> max_attempts reached -> dead

    refreshed = db.get(ProvisioningJob, job_id)
    assert refreshed.status == "dead"
    assert refreshed.attempt_number == 2


def test_process_due_provisioning_jobs_treats_a_409_as_skipped_exists(db: Session, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import tidybridge.provisioning as provisioning_module
    from tidybridge.webhook_worker import process_due_provisioning_jobs

    class _ConflictHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(409)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _ConflictHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", f"http://127.0.0.1:{port}/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()
    job = enqueue_provisioning(db, record)
    db.commit()
    job_id = job.id

    process_due_provisioning_jobs(db)
    server.shutdown()

    refreshed = db.get(ProvisioningJob, job_id)
    assert refreshed.status == "skipped_exists"  # terminal, not retried
    assert refreshed.attempt_number == 1


def test_process_due_provisioning_jobs_succeeds_and_captures_remote_id(db: Session, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import tidybridge.provisioning as provisioning_module
    from tidybridge.webhook_worker import process_due_provisioning_jobs

    class _CreatedHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            body = b'{"id": "usr_8f3a"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _CreatedHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", f"http://127.0.0.1:{port}/Users")
    record = ClientRecord(source_file="test.csv", full_name="Ada Lovelace", email="ada@example.com")
    db.add(record)
    db.flush()
    job = enqueue_provisioning(db, record)
    db.commit()
    job_id = job.id

    process_due_provisioning_jobs(db)
    server.shutdown()

    refreshed = db.get(ProvisioningJob, job_id)
    assert refreshed.status == "done"
    assert refreshed.remote_id == "usr_8f3a"
