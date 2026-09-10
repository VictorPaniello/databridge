"""Rate limiting is disabled globally in conftest.py so the rest of the
suite isn't tripped up by it (almost every other test registers and logs
in an engineer). This file re-enables it just for these tests, to prove
the limit set in main.py actually rejects requests - not just that the
Limiter object exists and looks configured correctly."""

from __future__ import annotations

from fastapi.testclient import TestClient

from databridge.main import app, limiter


def test_login_is_rate_limited_after_repeated_attempts():
    limiter.enabled = True
    try:
        client = TestClient(app)
        statuses = [
            client.post(
                "/auth/jwt/login",
                data={"username": "nobody@example.com", "password": "wrong"},
            ).status_code
            for _ in range(6)
        ]
    finally:
        limiter.enabled = False

    # The first 5 are rejected for a normal reason (no such user/wrong
    # password) - what this test actually cares about is that the 6th,
    # within the same minute, gets cut off by the limiter (429) instead of
    # being evaluated at all.
    assert statuses[:5] == [400, 400, 400, 400, 400]
    assert statuses[5] == 429


def test_register_is_rate_limited_after_repeated_attempts():
    limiter.enabled = True
    try:
        client = TestClient(app)
        statuses = [
            client.post(
                "/auth/register",
                json={
                    "email": f"ratelimit-{i}@example.com",
                    "password": "Some-Password-123!",
                    "first_name": "Rate",
                    "last_name": "Limit",
                },
            ).status_code
            for i in range(6)
        ]
    finally:
        limiter.enabled = False

    assert statuses[:5] == [201, 201, 201, 201, 201]
    assert statuses[5] == 429
