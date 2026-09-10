"""Verifies the webhook signature against a real local HTTP server, not a
mocked transport - the whole point is proving a real receiver can
recompute the same signature from the raw bytes it actually received, the
same thing a real integration partner's receiver would have to do."""

from __future__ import annotations

import hashlib
import hmac
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from databridge.webhooks import sign_payload

WEBHOOK_SECRET = "test-webhook-secret-for-signature-verification"


class _CapturingHandler(BaseHTTPRequestHandler):
    """Records exactly what it received (headers + raw body) so the test
    can verify the signature the same way a real receiver would - by
    recomputing it from the literal bytes on the wire, not from a
    Python dict the sender happened to construct it from."""

    received: list[dict] = []

    def do_POST(self):  # noqa: N802 - http.server's own naming convention
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CapturingHandler.received.append(
            {"body": body, "signature": self.headers.get("X-Databridge-Signature-256")}
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # silence the default request logging
        pass


@pytest.fixture
def webhook_receiver(monkeypatch):
    _CapturingHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import databridge.config as config_module

    monkeypatch.setattr(config_module.settings, "webhook_url", f"http://127.0.0.1:{port}/")
    monkeypatch.setattr(config_module.settings, "webhook_secret", WEBHOOK_SECRET)

    yield _CapturingHandler
    server.shutdown()


def _upload(client: TestClient):
    from pathlib import Path

    fixture = Path(__file__).parent.parent / "examples" / "messy_clients.csv"
    with open(fixture, "rb") as f:
        return client.post("/records/upload", files={"file": ("messy_clients.csv", f, "text/csv")})


def test_receiver_can_verify_the_real_signature(client: TestClient, webhook_receiver):
    response = _upload(client)
    assert response.status_code == 200

    assert webhook_receiver.received, "webhook receiver never got a request"
    delivery = webhook_receiver.received[0]

    # This is exactly what a real receiver's own verification code would
    # do: recompute HMAC-SHA256 over the raw body it received, using the
    # secret both sides agreed on out of band, and compare to the header.
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), delivery["body"], hashlib.sha256
    ).hexdigest()
    assert delivery["signature"] == expected


def test_tampered_body_fails_verification(client: TestClient, webhook_receiver):
    """Proves the signature actually protects integrity, not just presence
    - a receiver that (correctly) recomputes the HMAC over a body an
    attacker modified in transit must see a mismatch."""
    _upload(client)
    delivery = webhook_receiver.received[0]

    tampered_body = delivery["body"] + b" tampered"
    recomputed = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), tampered_body, hashlib.sha256
    ).hexdigest()
    assert recomputed != delivery["signature"]


def test_sign_payload_matches_a_reference_hmac_implementation():
    body = b'{"event": "test"}'
    secret = "some-secret"
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign_payload(body, secret) == expected


class _FlakyHandler(BaseHTTPRequestHandler):
    """A real local HTTP server (not a mock) that fails its first N
    requests with a 500 and succeeds on every request after that - proves
    notify_new_record() actually retries against a real receiver, the
    same way test_webhooks.py's other tests prove the signature against a
    real one, rather than trusting a mocked httpx.post() to reflect what
    a real flaky endpoint does."""

    fail_first_n: int = 0
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _FlakyHandler.received.append({"body": body})
        if len(_FlakyHandler.received) <= _FlakyHandler.fail_first_n:
            self.send_response(500)
        else:
            self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def flaky_webhook_receiver(monkeypatch):
    """Same shape as webhook_receiver above, but backed by _FlakyHandler
    and with retry backoff shrunk to keep the test fast - the backoff
    formula itself (webhooks.py's _backoff_seconds) is unaffected, only
    its base delay is, the same way test_upload_exceeding_size_limit
    shrinks _MAX_UPLOAD_BYTES instead of building a real multi-MB file."""
    _FlakyHandler.received = []
    _FlakyHandler.fail_first_n = 0
    server = HTTPServer(("127.0.0.1", 0), _FlakyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import databridge.config as config_module

    monkeypatch.setattr(config_module.settings, "webhook_url", f"http://127.0.0.1:{port}/")
    monkeypatch.setattr(config_module.settings, "webhook_secret", None)
    monkeypatch.setattr(config_module.settings, "webhook_retry_backoff_seconds", 0.01)

    yield _FlakyHandler
    server.shutdown()


def _upload_single_row(client: TestClient):
    """A one-row CSV (same header shape as examples/messy_clients.csv) so
    exactly one webhook delivery sequence fires - messy_clients.csv has
    5 rows, all newly ingested, which would fire 5 independent retry
    sequences against the shared flaky_webhook_receiver and make its raw
    request count mean "5 records x N attempts" instead of just N."""
    csv_body = (
        "Customer,Contact Email,Order Date,Order Total,Mobile Number\n"
        "Ada Lovelace,ada@shop.com,2026-09-01,100.00,+34 600 00 00 00\n"
    )
    return client.post(
        "/records/upload",
        files={"file": ("single.csv", csv_body.encode(), "text/csv")},
    )


def test_retries_and_eventually_succeeds_against_a_real_flaky_receiver(
    client: TestClient, flaky_webhook_receiver
):
    flaky_webhook_receiver.fail_first_n = 2  # 500, 500, then a real 200

    response = _upload_single_row(client)
    record_id = response.json()["records"][0]["id"]

    assert len(flaky_webhook_receiver.received) == 3  # first try + 2 retries

    deliveries = client.get(f"/records/{record_id}/webhooks").json()
    assert [d["attempt_number"] for d in deliveries] == [1, 2, 3]
    assert [d["success"] for d in deliveries] == [False, False, True]
    assert deliveries[0]["status_code"] == 500
    assert deliveries[2]["status_code"] == 200


def test_gives_up_after_max_attempts_and_logs_every_one(
    client: TestClient, flaky_webhook_receiver
):
    flaky_webhook_receiver.fail_first_n = 999  # never succeeds

    response = _upload_single_row(client)
    record_id = response.json()["records"][0]["id"]

    # settings.webhook_max_attempts defaults to 3 - every one of them was
    # actually tried against the real receiver, not just the first.
    assert len(flaky_webhook_receiver.received) == 3

    deliveries = client.get(f"/records/{record_id}/webhooks").json()
    assert [d["attempt_number"] for d in deliveries] == [1, 2, 3]
    assert all(d["success"] is False for d in deliveries)


def test_replay_sends_a_fresh_delivery_on_demand(client: TestClient, webhook_receiver):
    record_id = _upload(client).json()["records"][0]["id"]
    assert len(webhook_receiver.received) == 5  # one per row in messy_clients.csv

    response = client.post(f"/records/{record_id}/webhooks/replay")
    assert response.status_code == 200
    assert response.json()["success"] is True

    # The original delivery plus exactly one new one for this record -
    # not a second full ingest, not another retry sequence.
    assert len(webhook_receiver.received) == 6
    deliveries = client.get(f"/records/{record_id}/webhooks").json()
    assert [d["attempt_number"] for d in deliveries] == [1, 1]  # each its own attempt 1


def test_replay_retries_on_a_transient_failure_the_same_as_a_real_delivery(
    client: TestClient, flaky_webhook_receiver
):
    record_id = _upload_single_row(client).json()["records"][0]["id"]
    assert len(flaky_webhook_receiver.received) == 1  # succeeded first try (fail_first_n=0)

    # fail_first_n counts every request this handler has ever seen, not a
    # fresh "next N requests" window - it already saw 1 (the successful
    # ingest-time delivery above), so 2 means "the replay's own first
    # attempt (the 2nd request overall) fails, its retry (the 3rd)
    # succeeds".
    flaky_webhook_receiver.fail_first_n = 2
    response = client.post(f"/records/{record_id}/webhooks/replay")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["attempt_number"] == 2  # failed once, succeeded on the retry
    assert len(flaky_webhook_receiver.received) == 3  # 1 original + 2 for the replay


def test_replay_without_a_configured_webhook_url_is_rejected(client: TestClient):
    record_id = _upload(client).json()["records"][0]["id"]  # no webhook_receiver fixture here
    response = client.post(f"/records/{record_id}/webhooks/replay")
    assert response.status_code == 400


def test_replay_respects_ownership(
    client: TestClient, other_client: TestClient, webhook_receiver
):
    record_id = _upload(client).json()["records"][0]["id"]
    response = other_client.post(f"/records/{record_id}/webhooks/replay")
    assert response.status_code == 404  # existence of the record isn't revealed either


def test_replay_unknown_record_returns_404(client: TestClient, webhook_receiver):
    response = client.post("/records/00000000-0000-0000-0000-000000000000/webhooks/replay")
    assert response.status_code == 404
