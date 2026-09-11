"""DELETE /users/me: an engineer's real, self-service right to erase their
own account, not just individual client records - see main.py's docstring
on delete_own_account for why this needed its own endpoint (fastapi-users'
own DELETE /users/{id} is superuser-only, an admin deleting someone else's
account) and its own migration (ON DELETE CASCADE didn't exist on any of
the FKs pointing at users.id until then)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from tidybridge.models import ClientRecord, IngestionRun, WebhookDelivery

FIXTURES = Path(__file__).parent.parent / "examples"


def _upload(client: TestClient):
    with open(FIXTURES / "messy_clients.csv", "rb") as f:
        return client.post("/records/upload", files={"file": ("messy_clients.csv", f, "text/csv")})


def test_delete_own_account_returns_204_and_self_invalidates_the_token(client: TestClient):
    response = client.delete("/users/me")
    assert response.status_code == 204

    # The same bearer token, one request later - JWTs are stateless, so
    # this only fails because current_active_user looks the user id back
    # up and finds nothing, not because the token itself was revoked.
    assert client.get("/users/me").status_code == 401


def test_delete_own_account_requires_authentication():
    from tidybridge.main import app

    unauthenticated = TestClient(app)
    assert unauthenticated.delete("/users/me").status_code == 401


def test_delete_own_account_cascades_to_owned_data(client: TestClient, db):
    body = _upload(client).json()
    run_id = body["ingestion_run_id"]
    record_id = body["records"][0]["id"]
    db.add(WebhookDelivery(record_id=record_id, url="http://example.com", success=True))
    db.commit()

    response = client.delete("/users/me")
    assert response.status_code == 204

    # Real rows, actually gone - not a soft-delete flag some other query
    # could still surface, and not left orphaned by a half-finished
    # cascade (the whole point of adding ondelete="cascade" rather than
    # deleting each table by hand in application code).
    assert (
        db.execute(select(IngestionRun).where(IngestionRun.id == run_id)).scalar_one_or_none()
        is None
    )
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


def test_delete_own_account_does_not_touch_another_engineers_data(
    client: TestClient, other_client: TestClient, db
):
    my_run_id = _upload(client).json()["ingestion_run_id"]
    their_run_id = _upload(other_client).json()["ingestion_run_id"]

    response = client.delete("/users/me")
    assert response.status_code == 204

    assert (
        db.execute(select(IngestionRun).where(IngestionRun.id == my_run_id)).scalar_one_or_none()
        is None
    )
    # The other engineer's data survives untouched - deleting one account
    # must never cascade past that one account's own owned rows.
    assert (
        db.execute(select(IngestionRun).where(IngestionRun.id == their_run_id)).scalar_one_or_none()
        is not None
    )
    assert other_client.get("/ingestion-runs").status_code == 200
