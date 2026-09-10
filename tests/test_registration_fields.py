"""first_name/last_name/phone on User (auth_models.py) - added so the
frontend can show a real greeting instead of just the email. Required at
registration (first_name/last_name) via UserCreate's field constraints;
phone stays optional."""

from __future__ import annotations

from fastapi.testclient import TestClient

from databridge.main import app


def test_registration_requires_first_and_last_name():
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={"email": "no-name@example.com", "password": "Some-Password-123!"},
    )
    assert response.status_code == 422


def test_registration_persists_and_returns_the_name_fields():
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": "named-user@example.com",
            "password": "Some-Password-123!",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "phone": "+34600000000",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["first_name"] == "Ada"
    assert body["last_name"] == "Lovelace"
    assert body["phone"] == "+34600000000"


def test_phone_is_optional():
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": "no-phone@example.com",
            "password": "Some-Password-123!",
            "first_name": "No",
            "last_name": "Phone",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["phone"] is None
