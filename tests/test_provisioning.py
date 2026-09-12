"""SCIM-shaped payload building from a configured field mapping - see
provisioning.py's build_scim_payload docstring for the "true"/"false"
literal special-case and the full_name-split-on-first-space limitation,
both called out explicitly in the spec."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tidybridge.models import ClientRecord, ProvisioningJob
from tidybridge.provisioning import _load_mapping, build_scim_payload


def _upload_single_row(client: TestClient):
    csv_body = (
        "Customer,Contact Email,Order Date,Order Total,Mobile Number\n"
        "Ada Lovelace,ada@shop.com,2026-09-01,100.00,+34 600 00 00 00\n"
    )
    return client.post(
        "/records/upload", files={"file": ("single.csv", csv_body.encode(), "text/csv")}
    )


def test_default_mapping_file_loads_the_documented_scim_shape():
    mapping = _load_mapping()
    assert mapping == {
        "userName": "email",
        "name.givenName": "full_name",
        "name.familyName": "full_name",
        "emails[0].value": "email",
        "active": "true",
    }


def test_build_scim_payload_maps_fields_into_the_documented_shape():
    record = ClientRecord(
        id=uuid.uuid4(),
        source_file="test.csv",
        full_name="Grace Hopper",
        email="grace@example.com",
    )
    mapping = _load_mapping()

    payload = build_scim_payload(record, mapping)

    assert payload == {
        "userName": "grace@example.com",
        "name": {"givenName": "Grace", "familyName": "Hopper"},
        "emails": [{"value": "grace@example.com"}],
        "active": True,
    }


def test_build_scim_payload_splits_a_single_word_name_on_both_parts():
    # "documented limitation" (spec) - a name with no space has nothing
    # to put in familyName, so it repeats into both.
    record = ClientRecord(
        id=uuid.uuid4(), source_file="test.csv", full_name="Cher", email="c@e.com"
    )
    mapping = _load_mapping()

    payload = build_scim_payload(record, mapping)

    assert payload["name"] == {"givenName": "Cher", "familyName": "Cher"}


def test_upload_enqueues_a_provisioning_job_when_a_url_is_configured(
    client: TestClient, db: Session, monkeypatch
):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    job = db.execute(
        select(ProvisioningJob).where(ProvisioningJob.record_id == record_id)
    ).scalar_one()
    assert job.status == "pending"


def test_upload_does_not_enqueue_provisioning_without_a_configured_url(
    client: TestClient, db: Session
):
    # PROVISIONING_URL is unset in tests (see conftest.py) - hermetic by default.
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    job = db.execute(
        select(ProvisioningJob).where(ProvisioningJob.record_id == record_id)
    ).scalar_one_or_none()
    assert job is None


def test_provisioning_status_is_not_configured_without_a_url(client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = client.get(f"/records/{record_id}/provisioning-status")
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


def test_provisioning_status_is_pending_before_the_worker_runs(client: TestClient, monkeypatch):
    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    response = client.get(f"/records/{record_id}/provisioning-status")
    assert response.json()["status"] == "pending"
    assert response.json()["attempt_number"] == 1
    assert response.json()["remote_id"] is None


def test_provisioning_status_respects_ownership(client: TestClient, other_client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = other_client.get(f"/records/{record_id}/provisioning-status")
    assert response.status_code == 404


def test_get_provisioning_returns_the_attempt_history(client: TestClient, db: Session, monkeypatch):
    import tidybridge.provisioning as provisioning_module
    from tidybridge.webhook_worker import process_due_provisioning_jobs

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    process_due_provisioning_jobs(db)

    attempts = client.get(f"/records/{record_id}/provisioning").json()
    assert len(attempts) == 1
    assert attempts[0]["success"] is False
    assert attempts[0]["record_id"] == record_id


def test_replay_provisioning_resets_the_job_and_requires_a_configured_url(
    client: TestClient, monkeypatch
):
    record_id = _upload_single_row(client).json()["records"][0]["id"]

    no_url_response = client.post(f"/records/{record_id}/provisioning/replay")
    assert no_url_response.status_code == 400

    import tidybridge.provisioning as provisioning_module

    monkeypatch.setattr(provisioning_module.settings, "provisioning_url", "http://127.0.0.1:1/Users")
    response = client.post(f"/records/{record_id}/provisioning/replay")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["attempt_number"] == 1


def test_replay_provisioning_respects_ownership(client: TestClient, other_client: TestClient):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    response = other_client.post(f"/records/{record_id}/provisioning/replay")
    assert response.status_code == 404


def test_provisioning_endpoints_require_auth():
    from tidybridge.main import app

    unauthenticated = TestClient(app)
    assert (
        unauthenticated.get(
            "/records/00000000-0000-0000-0000-000000000000/provisioning-status"
        ).status_code
        == 401
    )
    assert (
        unauthenticated.get("/records/00000000-0000-0000-0000-000000000000/provisioning").status_code
        == 401
    )
    assert (
        unauthenticated.post(
            "/records/00000000-0000-0000-0000-000000000000/provisioning/replay"
        ).status_code
        == 401
    )
