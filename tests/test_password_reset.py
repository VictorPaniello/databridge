"""Forgot-password / reset-password (auth.py's UserManager.on_after_
forgot_password + fastapi-users' own reset-password router, wired in
main.py).

Tests set a fake RESEND_API_KEY and patch httpx.AsyncClient.post so they
exercise the real Resend-send code path (not just the no-key dev-fallback
that logs the link instead) - a stub records what was actually about to be
sent instead of hitting the real Resend API, and the test pulls the real,
single-use reset token straight out of that captured request body."""

from __future__ import annotations

import re
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

TOKEN_RE = re.compile(r"reset-password\?token=([^\"&\s]+)")


@pytest.fixture(autouse=True)
def _fake_resend(monkeypatch):
    """Gives every test in this file a configured "provider" without ever
    making a real network call - captures each outgoing send in `sent`
    (a list of the JSON bodies httpx.AsyncClient.post was called with) so
    a test can pull the reset link out of the html it was about to mail."""
    from tidybridge.config import settings

    monkeypatch.setattr(settings, "resend_api_key", "re_test_fake_key")
    sent: list[dict] = []

    async def fake_post(self, url, *, headers=None, json=None, **kwargs):
        sent.append(json)
        return httpx.Response(200, json={"id": "fake-email-id"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return sent


def _register(client: TestClient, email: str, password: str) -> None:
    resp = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Reset",
            "last_name": "Test",
        },
    )
    assert resp.status_code == 201, resp.text


def _request_reset_token(sent: list[dict], client: TestClient, email: str) -> str:
    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 202
    assert sent, "on_after_forgot_password never called Resend"
    message = sent[-1]
    assert message["to"] == [email]
    match = TOKEN_RE.search(message["html"])
    assert match, f"no reset link found in outgoing email: {message['html']!r}"
    return match.group(1)


def test_forgot_password_on_unregistered_email_sends_nothing(_fake_resend):
    """No email sent (and, per fastapi-users' own router, no token even
    generated) for an address nobody registered - the generic 202 either
    way is what stops this endpoint from being usable to find out which
    emails are registered. See main.py's rate-limit comment."""
    from tidybridge.main import app

    client = TestClient(app)
    resp = client.post(
        "/auth/forgot-password", json={"email": f"nobody-{uuid.uuid4()}@example.com"}
    )
    assert resp.status_code == 202
    assert resp.json() == {"oauth_only": False}
    assert _fake_resend == []


def test_forgot_password_on_password_account_reports_oauth_only_false(_fake_resend):
    from tidybridge.main import app

    client = TestClient(app)
    email = f"has-password-{uuid.uuid4()}@example.com"
    _register(client, email, "Original-Password-1!")

    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 202
    assert resp.json() == {"oauth_only": False}
    assert _fake_resend, "should have sent an email - this account has a real password"


def test_forgot_password_on_oauth_only_account_sends_nothing(_fake_resend, db):
    """Simulates the has_password=False end state a real GitHub-only
    signup leaves a user in (auth.py's UserManager.create() is the only
    path that sets it True, and oauth_callback() never calls it - see
    that override's docstring) by flipping the flag directly rather than
    driving a real GitHub handshake, the same choice
    test_oauth_redirect.py's own comment explains for not re-testing that
    handshake here."""
    from tidybridge.auth_models import User
    from tidybridge.main import app

    client = TestClient(app)
    email = f"oauth-only-{uuid.uuid4()}@example.com"
    _register(client, email, "Original-Password-1!")

    user = db.query(User).filter(User.email == email).one()
    user.has_password = False
    db.commit()

    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 202
    assert resp.json() == {"oauth_only": True}
    assert _fake_resend == []


def test_reset_password_with_valid_token_changes_the_password(_fake_resend):
    from tidybridge.main import app

    client = TestClient(app)
    email = f"reset-{uuid.uuid4()}@example.com"
    old_password = "Original-Password-1!"
    new_password = "Brand-New-Password-2!"
    _register(client, email, old_password)

    token = _request_reset_token(_fake_resend, client, email)

    reset_resp = client.post(
        "/auth/reset-password", json={"token": token, "password": new_password}
    )
    assert reset_resp.status_code == 200, reset_resp.text

    # The whole point: sign in with the *new* password now works...
    login_new = client.post(
        "/auth/jwt/login", data={"username": email, "password": new_password}
    )
    assert login_new.status_code == 200, login_new.text

    # ...and the old one no longer does.
    login_old = client.post(
        "/auth/jwt/login", data={"username": email, "password": old_password}
    )
    assert login_old.status_code == 400


def test_reset_password_with_invalid_token_is_rejected():
    from tidybridge.main import app

    client = TestClient(app)
    resp = client.post(
        "/auth/reset-password", json={"token": "not-a-real-token", "password": "Whatever-1!"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "RESET_PASSWORD_BAD_TOKEN"


def test_reset_password_token_cannot_be_reused_after_a_successful_reset(_fake_resend):
    """The token embeds a fingerprint of the password hash it was issued
    against (fastapi-users' forgot_password()), so it self-invalidates the
    moment the password actually changes - no separate single-use
    bookkeeping needed. Covers replaying an old reset email/link after
    it's already been used once."""
    from tidybridge.main import app

    client = TestClient(app)
    email = f"reuse-{uuid.uuid4()}@example.com"
    _register(client, email, "Original-Password-1!")
    token = _request_reset_token(_fake_resend, client, email)

    first = client.post(
        "/auth/reset-password", json={"token": token, "password": "First-New-Pass-1!"}
    )
    assert first.status_code == 200, first.text

    replay = client.post(
        "/auth/reset-password", json={"token": token, "password": "Second-New-Pass-2!"}
    )
    assert replay.status_code == 400
    assert replay.json()["detail"] == "RESET_PASSWORD_BAD_TOKEN"


def test_reset_password_still_enforces_the_password_policy(_fake_resend):
    """UserManager.validate_password (test_password_policy.py covers it on
    registration) is a shared override - fastapi-users calls it on this
    path too, not just on /auth/register."""
    from tidybridge.main import app

    client = TestClient(app)
    email = f"weak-reset-{uuid.uuid4()}@example.com"
    _register(client, email, "Original-Password-1!")
    token = _request_reset_token(_fake_resend, client, email)

    resp = client.post("/auth/reset-password", json={"token": token, "password": "allweak"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "RESET_PASSWORD_INVALID_PASSWORD"


def test_forgot_password_falls_back_to_logging_when_no_provider_configured(monkeypatch, caplog):
    """The no-RESEND_API_KEY dev fallback (config.py's resend_api_key
    docstring) - separate from every other test in this file, which
    configures a fake key via the autouse fixture above."""
    from tidybridge.config import settings
    from tidybridge.main import app

    monkeypatch.setattr(settings, "resend_api_key", None)

    def fail_if_called(self, url, **kwargs):
        raise AssertionError("should not call Resend when no API key is configured")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_if_called)

    client = TestClient(app)
    email = f"no-provider-{uuid.uuid4()}@example.com"
    _register(client, email, "Original-Password-1!")

    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 202


def test_forgot_password_endpoint_is_rate_limited_after_repeated_attempts(_fake_resend):
    from tidybridge.main import app, limiter

    # Registered with the limiter still off - only /auth/forgot-password's
    # own quota is under test here. Doing this inside the enabled block
    # would also burn one of /auth/register's 5/minute against the same
    # shared (in-memory, session-lifetime) limiter storage that
    # test_rate_limit.py's own register test relies on starting fresh.
    client = TestClient(app)
    email = f"ratelimit-forgot-{uuid.uuid4()}@example.com"
    _register(client, email, "Original-Password-1!")

    limiter.enabled = True
    try:
        statuses = [
            client.post("/auth/forgot-password", json={"email": email}).status_code
            for _ in range(6)
        ]
    finally:
        limiter.enabled = False

    assert statuses[:5] == [202, 202, 202, 202, 202]
    assert statuses[5] == 429
