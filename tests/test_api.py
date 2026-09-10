from pathlib import Path

from fastapi.testclient import TestClient

from databridge.main import app

FIXTURES = Path(__file__).parent.parent / "examples"


def _upload(client: TestClient, filename: str = "messy_clients.csv"):
    with open(FIXTURES / filename, "rb") as f:
        return client.post("/records/upload", files={"file": (filename, f, "text/csv")})


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_cleans_and_persists_records(client: TestClient):
    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["rows_total"] == 5
    assert body["rows_clean"] == 2
    assert body["rows_flagged"] == 3
    assert len(body["records"]) == 5


def test_missing_optional_field_is_null_not_the_string_nan(client: TestClient):
    # Regression coverage for the None -> NaN pandas bug found while building
    # this project (fixed both upstream in tidycsv and here in ingest.py's
    # row access). A JSON response of "NaN" instead of null is exactly what
    # that bug looked like from the API's side.
    response = _upload(client)
    records = response.json()["records"]
    no_name_record = next(r for r in records if r["email"] == "noemail@shop.com")
    assert no_name_record["full_name"] is None
    assert no_name_record["phone"] is None


def test_flagged_issues_are_attached_to_the_correct_record(client: TestClient):
    records = _upload(client).json()["records"]
    invalid_email_record = next(r for r in records if r["email"] == "not-an-email")
    assert invalid_email_record["has_issues"] is True
    assert invalid_email_record["issues"] == [
        {"field": "email", "issue": "invalid email format"}
    ]

    bad_date_record = next(r for r in records if r["full_name"] == "Marco Rossi")
    assert bad_date_record["issues"] == [{"field": "signup_date", "issue": "unparseable date"}]


def test_reuploading_the_same_file_is_idempotent(client: TestClient):
    first = _upload(client)
    assert len(first.json()["records"]) == 5

    second = _upload(client)
    assert second.json()["records"] == []  # nothing new - already ingested by email

    all_records = client.get("/records").json()
    assert len(all_records) == 5  # not 10


def test_list_records_filters_by_has_issues(client: TestClient):
    _upload(client)
    flagged = client.get("/records", params={"has_issues": True}).json()
    clean = client.get("/records", params={"has_issues": False}).json()
    assert len(flagged) == 3
    assert len(clean) == 2


def test_get_single_record(client: TestClient):
    records = _upload(client).json()["records"]
    record_id = records[0]["id"]
    response = client.get(f"/records/{record_id}")
    assert response.status_code == 200
    assert response.json()["id"] == record_id


def test_get_unknown_record_returns_404(client: TestClient):
    response = client.get("/records/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_no_webhook_deliveries_when_webhook_url_unset(client: TestClient):
    records = _upload(client).json()["records"]
    record_id = records[0]["id"]
    deliveries = client.get(f"/records/{record_id}/webhooks").json()
    assert deliveries == []


def test_upload_without_a_token_is_rejected(client: TestClient):
    # `client` comes pre-authenticated (see conftest.py) - this checks the
    # protection actually exists by calling with no Authorization header.
    unauthenticated = TestClient(app)
    response = _upload(unauthenticated)
    assert response.status_code == 401


def test_engineer_cannot_see_another_engineers_records(
    client: TestClient, other_client: TestClient
):
    my_records = _upload(client).json()["records"]
    _upload(other_client)  # a second engineer uploads the same file independently

    # Each engineer's own duplicate-by-email check still fires - only
    # cross-engineer isolation is being tested here, not dedup.
    assert len(_upload(client).json()["records"]) == 0

    my_view = client.get("/records").json()
    other_view = other_client.get("/records").json()
    assert {r["id"] for r in my_view} == {r["id"] for r in my_records}
    assert {r["id"] for r in other_view}.isdisjoint({r["id"] for r in my_records})


def test_engineer_gets_404_not_403_for_another_engineers_record(
    client: TestClient, other_client: TestClient
):
    my_record_id = _upload(client).json()["records"][0]["id"]
    response = other_client.get(f"/records/{my_record_id}")
    assert response.status_code == 404  # existence of the record isn't revealed either

    response = other_client.get(f"/records/{my_record_id}/webhooks")
    assert response.status_code == 404
