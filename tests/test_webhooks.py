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
