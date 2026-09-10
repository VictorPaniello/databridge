"""Email verification (auth.py's UserManager.on_after_register/
on_after_request_verify + fastapi-users' own verify router, wired in
main.py) and the current_verified_active_user gate it feeds on
/records/* routes.

Same fake-Resend approach as test_password_reset.py: a configured
provider is faked via monkeypatch rather than mocked away entirely, so
these tests exercise the real send code path and pull the real,
fastapi-users-generated token out of the email that was actually about to
go out."""

from __future__ import annotations

import re
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

VERIFY_TOKEN_RE = re.compile(r"verify-email\?token=([^\"&\s]+)")


@pytest.fixture(autouse=True)
def _fake_resend(monkeypatch):
    from databridge.config import settings

    monkeypatch.setattr(settings, "resend_api_key", "re_test_fake_key")
    sent: list[dict] = []

    async def fake_post(self, url, *, headers=None, json=None, **kwargs):
        sent.append(json)
        return httpx.Response(200, json={"id": "fake-email-id"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return sent


def _register_and_login(client: TestClient, email: str, password: str) -> None:
    resp = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Verify",
            "last_name": "Test",
        },
    )
    assert resp.status_code == 201, resp.text
    login_resp = client.post("/auth/jwt/login", data={"username": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    client.headers.update({"Authorization": f"Bearer {login_resp.json()['access_token']}"})


def test_registration_sends_a_verification_email(_fake_resend):
    from databridge.main import app

    client = TestClient(app)
    email = f"register-verify-{uuid.uuid4()}@example.com"
    _register_and_login(client, email, "Original-Password-1!")

    assert _fake_resend, "registration should have sent a verification email"
    message = _fake_resend[-1]
    assert message["to"] == [email]
    assert VERIFY_TOKEN_RE.search(message["html"]), message["html"]


def test_records_are_blocked_until_email_is_verified_then_allowed_after(_fake_resend):
    """The actual point of this whole feature: an unverified, freshly
    registered engineer can sign in fine but can't touch records - and
    clicking the real emailed link (simulated by extracting its token
    the same way a person would click it) unblocks exactly that."""
    from databridge.main import app

    client = TestClient(app)
    email = f"blocked-until-verified-{uuid.uuid4()}@example.com"
    _register_and_login(client, email, "Original-Password-1!")

    blocked = client.get("/records")
    assert blocked.status_code == 403

    match = VERIFY_TOKEN_RE.search(_fake_resend[-1]["html"])
    assert match, f"no verify link found in outgoing email: {_fake_resend[-1]['html']!r}"
    token = match.group(1)

    verify_resp = client.post("/auth/verify", json={"token": token})
    assert verify_resp.status_code == 200, verify_resp.text
    assert verify_resp.json()["is_verified"] is True

    allowed = client.get("/records")
    assert allowed.status_code == 200


def test_get_me_stays_accessible_while_unverified_but_patch_is_blocked(_fake_resend):
    """GET /users/me deliberately stays on the plain active-only
    dependency (see auth.py's current_verified_active_user docstring) -
    it has to, or the frontend could never discover is_verified: false in
    the first place. PATCH /users/me has no such exception: an unverified
    engineer can see their own account is unverified, but can't touch it
    at all - no typo-fixing loophole, per main.py's split GET/PATCH
    routing."""
    from databridge.main import app

    client = TestClient(app)
    email = f"edit-while-unverified-{uuid.uuid4()}@example.com"
    _register_and_login(client, email, "Original-Password-1!")

    get_resp = client.get("/users/me")
    assert get_resp.status_code == 200
    assert get_resp.json()["is_verified"] is False

    patch_resp = client.patch("/users/me", json={"first_name": "Changed"})
    assert patch_resp.status_code == 403

    match = VERIFY_TOKEN_RE.search(_fake_resend[-1]["html"])
    token = match.group(1)
    verify_resp = client.post("/auth/verify", json={"token": token})
    assert verify_resp.status_code == 200, verify_resp.text

    patch_after_verify = client.patch("/users/me", json={"first_name": "Changed"})
    assert patch_after_verify.status_code == 200, patch_after_verify.text
    assert patch_after_verify.json()["first_name"] == "Changed"


def test_users_admin_id_routes_are_gone_not_just_unused(_fake_resend):
    """Regression test for a real bug hit while building this: main.py
    originally dropped only the library's PATCH /me, leaving its
    superuser-only PATCH /{id} registered - which then silently caught
    "PATCH /users/me" itself (id="me") before the dedicated /users/me
    route below ever got a chance, since it was the only remaining PATCH
    route in that sub-router. A superuser check failing for a normal user
    403s with the exact same bare "Forbidden" body a verification failure
    does, which made it look at first like verification itself was
    broken. Fixed by dropping all three /{id} admin routes outright -
    this app has no superuser flow using them anyway - not just working
    around the one path they happened to shadow."""
    from databridge.main import app

    client = TestClient(app)
    email = f"no-id-shadow-{uuid.uuid4()}@example.com"
    _register_and_login(client, email, "Original-Password-1!")
    token = VERIFY_TOKEN_RE.search(_fake_resend[-1]["html"]).group(1)
    verify_resp = client.post("/auth/verify", json={"token": token})
    assert verify_resp.status_code == 200, verify_resp.text

    patch_resp = client.patch("/users/me", json={"first_name": "Shadowed"})
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["first_name"] == "Shadowed"

    assert client.get("/users/some-id").status_code == 404
    assert client.patch("/users/some-id", json={}).status_code == 404
    assert client.delete("/users/some-id").status_code == 404


def test_verify_with_invalid_token_is_rejected():
    from databridge.main import app

    client = TestClient(app)
    resp = client.post("/auth/verify", json={"token": "not-a-real-token"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "VERIFY_USER_BAD_TOKEN"


def test_verify_token_cannot_be_reused_after_success(_fake_resend):
    from databridge.main import app

    client = TestClient(app)
    email = f"reuse-verify-{uuid.uuid4()}@example.com"
    _register_and_login(client, email, "Original-Password-1!")
    token = VERIFY_TOKEN_RE.search(_fake_resend[-1]["html"]).group(1)

    first = client.post("/auth/verify", json={"token": token})
    assert first.status_code == 200, first.text

    replay = client.post("/auth/verify", json={"token": token})
    assert replay.status_code == 400
    assert replay.json()["detail"] == "VERIFY_USER_ALREADY_VERIFIED"


def test_already_verified_account_gets_no_new_verification_email(_fake_resend, db):
    """Covers the same no-op UserManager.request_verify() hits for a
    GitHub OAuth signup (created with is_verified already True - main.py
    passes is_verified_by_default=True to that router) without driving a
    real GitHub handshake, the same tradeoff test_password_reset.py's
    OAuth-only simulation explains."""
    from databridge.auth_models import User
    from databridge.main import app

    client = TestClient(app)
    email = f"pre-verified-{uuid.uuid4()}@example.com"
    _register_and_login(client, email, "Original-Password-1!")
    _fake_resend.clear()

    user = db.query(User).filter(User.email == email).one()
    user.is_verified = True
    db.commit()

    resp = client.post("/auth/request-verify-token", json={"email": email})
    assert resp.status_code == 202
    assert _fake_resend == []


def test_request_verify_token_endpoint_is_rate_limited_after_repeated_attempts(_fake_resend):
    from databridge.main import app, limiter

    client = TestClient(app)
    email = f"ratelimit-verify-{uuid.uuid4()}@example.com"
    # Registered with the limiter still off, same reasoning as
    # test_password_reset.py's own rate-limit test: this account's
    # /auth/register call shouldn't burn any of that route's separate
    # 5/minute quota against shared limiter storage.
    resp = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "Original-Password-1!",
            "first_name": "Rate",
            "last_name": "Limit",
        },
    )
    assert resp.status_code == 201, resp.text

    limiter.enabled = True
    try:
        statuses = [
            client.post("/auth/request-verify-token", json={"email": email}).status_code
            for _ in range(6)
        ]
    finally:
        limiter.enabled = False

    assert statuses[:5] == [202, 202, 202, 202, 202]
    assert statuses[5] == 429
