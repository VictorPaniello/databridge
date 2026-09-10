"""Real CORS preflight against the real app - not asserting the middleware
is configured, but that a browser calling from the configured frontend
origin would actually be let through, and one from anywhere else wouldn't."""

from __future__ import annotations

from fastapi.testclient import TestClient

from databridge.config import settings
from databridge.main import app


def test_preflight_from_the_configured_frontend_origin_is_allowed():
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": settings.frontend_url,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers["access-control-allow-origin"] == settings.frontend_url


def test_preflight_from_an_unrelated_origin_is_not_allowed():
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": "https://some-other-site.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers
