"""GET /records/export - a plain-text CSV download of the caller's own
records, not the paginated JSON GET /records returns. Real content
verified with Python's own csv.DictReader over the raw response body,
not just status code / content-type, since a header alone doesn't prove
the actual rows are right."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from tidybridge.main import app

CLEAN_AND_FLAGGED_CSV = (
    "Customer,Contact Email,Order Date,Order Total,Mobile Number\n"
    "Grace Hopper,grace@example.com,2026-09-01,120.00,+1 212 555 0101\n"
    "Ada Yonath,not-an-email,2026-09-05,45.00,+1 212 555 0105\n"
)


def _upload_csv(client: TestClient, csv_body: str, filename: str = "export_test.csv"):
    return client.post(
        "/records/upload", files={"file": (filename, csv_body.encode(), "text/csv")}
    )


def _parse_csv(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode())))


def test_export_returns_csv_with_expected_columns_and_rows(client: TestClient):
    _upload_csv(client, CLEAN_AND_FLAGGED_CSV)

    response = client.get("/records/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    rows = _parse_csv(response.content)
    assert len(rows) == 2
    assert {r["full_name"] for r in rows} == {"Grace Hopper", "Ada Yonath"}

    clean = next(r for r in rows if r["full_name"] == "Grace Hopper")
    assert clean["email"] == "grace@example.com"
    assert clean["has_issues"] == "False"
    assert clean["issues"] == ""

    flagged = next(r for r in rows if r["full_name"] == "Ada Yonath")
    assert flagged["has_issues"] == "True"
    assert "email" in flagged["issues"]


def test_export_respects_ingestion_run_id_filter(client: TestClient):
    first_run = _upload_csv(client, CLEAN_AND_FLAGGED_CSV, "first.csv").json()["ingestion_run_id"]
    second_csv = (
        "Customer,Contact Email,Order Date,Order Total,Mobile Number\n"
        "Nikola Tesla,nikola@example.com,2026-09-02,89.99,+1 212 555 0102\n"
    )
    _upload_csv(client, second_csv, "second.csv")

    response = client.get(f"/records/export?ingestion_run_id={first_run}")
    rows = _parse_csv(response.content)
    assert {r["full_name"] for r in rows} == {"Grace Hopper", "Ada Yonath"}


def test_export_only_includes_the_callers_own_records(
    client: TestClient, other_client: TestClient
):
    _upload_csv(client, CLEAN_AND_FLAGGED_CSV)

    response = other_client.get("/records/export")
    rows = _parse_csv(response.content)
    assert rows == []


def test_export_requires_auth(client: TestClient):
    # `client` comes pre-authenticated (see conftest.py) - this checks the
    # protection actually exists by calling with no Authorization header.
    unauthenticated = TestClient(app)
    response = unauthenticated.get("/records/export")
    assert response.status_code == 401
