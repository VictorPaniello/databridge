"""IngestionRun: the persisted, queryable summary of what an upload
actually did - before this, that summary only ever existed in the
IngestResult HTTP response, gone the moment nobody was looking at it."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent.parent / "examples"


def _upload(client: TestClient, filename: str = "messy_clients.csv"):
    with open(FIXTURES / filename, "rb") as f:
        return client.post("/records/upload", files={"file": (filename, f, "text/csv")})


def test_upload_response_references_a_real_persisted_run(client: TestClient):
    response = _upload(client)
    body = response.json()
    run_id = body["ingestion_run_id"]
    assert run_id is not None

    run = client.get(f"/ingestion-runs/{run_id}").json()
    assert run["source_file"] == "messy_clients.csv"
    assert run["rows_total"] == 5
    assert run["rows_clean"] == body["rows_clean"]
    assert run["rows_flagged"] == body["rows_flagged"]
    assert run["rows_dropped_duplicates"] == body["rows_dropped_duplicates"]
    assert run["rows_skipped_existing"] == 0  # nothing pre-existed yet


def test_every_row_is_accounted_for_exactly_once(client: TestClient):
    # The invariant this whole feature exists to make true and checkable:
    # every row in the file is exactly one of these four things, nothing
    # silently unaccounted for.
    run = client.get(
        f"/ingestion-runs/{_upload(client).json()['ingestion_run_id']}"
    ).json()
    accounted_for = (
        run["rows_clean"]
        + run["rows_flagged"]
        + run["rows_dropped_duplicates"]
        + run["rows_skipped_existing"]
    )
    assert accounted_for == run["rows_total"]


def test_reuploading_creates_a_second_run_where_every_row_is_skipped_existing(
    client: TestClient,
):
    _upload(client)  # first upload: 5 new records
    second = _upload(client)  # same file again: nothing new

    assert second.json()["records"] == []
    run = client.get(f"/ingestion-runs/{second.json()['ingestion_run_id']}").json()
    assert run["rows_total"] == 5
    assert run["rows_clean"] == 0
    assert run["rows_flagged"] == 0
    assert run["rows_skipped_existing"] == 5  # this is the row-accounting gap this closes


def test_ingestion_run_links_back_to_the_records_it_created(client: TestClient):
    body = _upload(client).json()
    run_id = body["ingestion_run_id"]

    for record in body["records"]:
        assert record["ingestion_run_id"] == run_id

    # Drills from "this run" to "exactly these records", not just a
    # count - the whole point of persisting the link.
    linked = client.get("/records", params={"ingestion_run_id": run_id}).json()
    assert linked["total"] == 5
    assert {r["id"] for r in linked["items"]} == {r["id"] for r in body["records"]}


def test_list_ingestion_runs_is_paginated_and_scoped_to_the_caller(
    client: TestClient, other_client: TestClient
):
    _upload(client)
    _upload(other_client, "messy_clients.csv")

    page = client.get("/ingestion-runs").json()
    assert page["total"] == 1  # not 2 - other_client's run isn't this caller's
    assert len(page["items"]) == 1
    assert page["items"][0]["source_file"] == "messy_clients.csv"


def test_get_unknown_ingestion_run_returns_404(client: TestClient):
    response = client.get("/ingestion-runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_another_engineers_ingestion_run_returns_404_not_403(
    client: TestClient, other_client: TestClient
):
    run_id = _upload(client).json()["ingestion_run_id"]
    response = other_client.get(f"/ingestion-runs/{run_id}")
    assert response.status_code == 404  # existence of the run isn't revealed either
