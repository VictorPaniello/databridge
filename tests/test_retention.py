"""sweep_expired_client_data: the real, automated enforcement behind the
Privacy Policy's data-retention promise. Uses the real test database
(via the `client`/`db` fixtures), not mocks - the whole point is proving
the deletion actually happens, cascades correctly, and stops at the
right boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from databridge.auth_models import User
from databridge.models import ClientRecord, IngestionRun, WebhookDelivery
from databridge.retention import sweep_expired_client_data

FIXTURES = Path(__file__).parent.parent / "examples"


def _upload(client: TestClient):
    with open(FIXTURES / "messy_clients.csv", "rb") as f:
        return client.post("/records/upload", files={"file": ("messy_clients.csv", f, "text/csv")})


def _backdate(db, model, row_id, days_old: int):
    cutoff = datetime.now(UTC) - timedelta(days=days_old)
    db.execute(update(model).where(model.id == row_id).values(created_at=cutoff))
    db.commit()


def test_sweep_deletes_a_run_and_cascades_to_its_records_and_deliveries(
    client: TestClient, db
):
    body = _upload(client).json()
    run_id = body["ingestion_run_id"]
    record_id = body["records"][0]["id"]
    db.add(WebhookDelivery(record_id=record_id, url="http://example.com", success=True))
    db.commit()

    _backdate(db, IngestionRun, run_id, days_old=400)  # older than the 365-day default

    result = sweep_expired_client_data(db)

    assert result["deleted_runs"] == 1
    assert (
        db.execute(select(IngestionRun).where(IngestionRun.id == run_id)).scalar_one_or_none()
        is None
    )
    # Cascaded, not just the run row itself - the whole point of relying
    # on ON DELETE CASCADE (models.py) instead of deleting each table by
    # hand here.
    assert (
        db.execute(select(ClientRecord).where(ClientRecord.id == record_id)).scalar_one_or_none()
        is None
    )
    assert (
        db.execute(select(WebhookDelivery).where(WebhookDelivery.record_id == record_id))
        .scalars()
        .all()
        == []
    )


def test_sweep_leaves_recent_runs_alone(client: TestClient, db):
    body = _upload(client).json()
    run_id = body["ingestion_run_id"]
    # No backdating - this run is as fresh as the moment it was created.

    result = sweep_expired_client_data(db)

    assert result["deleted_runs"] == 0
    assert (
        db.execute(select(IngestionRun).where(IngestionRun.id == run_id)).scalar_one_or_none()
        is not None
    )


def test_sweep_deletes_orphaned_records_with_no_ingestion_run(client: TestClient, db):
    """Records ingested before ingestion_run_id existed have no run to
    cascade from - the sweep's direct ClientRecord pass is what catches
    these, not just the IngestionRun deletion above."""
    body = _upload(client).json()
    record_id = body["records"][0]["id"]
    db.execute(
        update(ClientRecord).where(ClientRecord.id == record_id).values(ingestion_run_id=None)
    )
    db.commit()
    _backdate(db, ClientRecord, record_id, days_old=400)

    result = sweep_expired_client_data(db)

    assert result["deleted_orphan_records"] == 1
    assert (
        db.execute(select(ClientRecord).where(ClientRecord.id == record_id)).scalar_one_or_none()
        is None
    )


def test_sweep_never_touches_the_owning_user_account(client: TestClient, db):
    """Retention applies only to client data (what an engineer uploads
    about their own clients) - never to the engineer's own account. See
    DELETE /users/me for the (separate, user-initiated) way an account
    itself goes away."""
    body = _upload(client).json()
    run_id = body["ingestion_run_id"]
    user_id = db.execute(
        select(IngestionRun.owner_id).where(IngestionRun.id == run_id)
    ).scalar_one()
    _backdate(db, IngestionRun, run_id, days_old=400)

    sweep_expired_client_data(db)

    # .unique() is required here (not on the other queries in this file):
    # User has a joined-eager-loaded collection relationship (its linked
    # OAuth accounts), which SQLAlchemy refuses to deduplicate silently.
    assert (
        db.execute(select(User).where(User.id == user_id)).unique().scalar_one_or_none()
        is not None
    )


def test_sweep_respects_a_custom_retention_window(client: TestClient, db, monkeypatch):
    import databridge.config as config_module

    monkeypatch.setattr(config_module.settings, "client_data_retention_days", 30)

    body = _upload(client).json()
    run_id = body["ingestion_run_id"]
    _backdate(db, IngestionRun, run_id, days_old=45)  # past 30 days, not past the 365 default

    result = sweep_expired_client_data(db)

    assert result["deleted_runs"] == 1
