"""UserManager.validate_password (auth.py) - fastapi-users applies no
strength requirement by default, which is what let a one-character
password through before this policy existed (found in a security
review)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "password,expected_reason_fragment",
    [
        ("Short1!", "at least 8 characters"),
        ("alllowercase123!", "uppercase letter"),
        ("ALLUPPERCASE123!", "lowercase letter"),
        ("NoDigitsHere!", "digit"),
        ("NoSpecial12345", "special character"),
    ],
)
def test_weak_password_is_rejected(password: str, expected_reason_fragment: str):
    from databridge.main import app

    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": "weak-password@example.com",
            "password": password,
            "first_name": "Weak",
            "last_name": "Password",
        },
    )
    assert response.status_code == 400
    reason = response.json()["detail"]["reason"]
    assert expected_reason_fragment in reason


def test_strong_password_is_accepted():
    from databridge.main import app

    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": "strong-password@example.com",
            "password": "Str0ng-Pass!",
            "first_name": "Strong",
            "last_name": "Password",
        },
    )
    assert response.status_code == 201
