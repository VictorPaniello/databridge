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


def test_content_disposition_is_exposed_to_cross_origin_javascript():
    """Content-Disposition isn't on the browser's default CORS-safelisted
    response headers - without allow_headers' expose_headers explicitly
    naming it, exportRecords() (api/client.ts) could read the response
    body fine but never see the filename the backend generated, silently
    falling back to a generic one instead."""
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": settings.frontend_url})
    assert "content-disposition" in response.headers["access-control-expose-headers"].lower()
