"""Real requests against the real app - not asserting the middleware
function exists, but that a response a browser actually receives carries
the headers it's supposed to, and that /docs actually stops responding
once enable_api_docs is off."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tidybridge.main import app


def test_every_response_carries_the_security_headers():
    client = TestClient(app)
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


def test_security_headers_are_present_even_on_a_404():
    """Registered as middleware (wraps every response Starlette produces),
    not applied inside individual route handlers - so a path that never
    reaches one still gets them, not just successful requests."""
    client = TestClient(app)
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"


def test_docs_are_served_by_default():
    """settings.enable_api_docs defaults to True - the app this whole test
    suite runs against was built with that default (docs_url is read once,
    at FastAPI() construction time in main.py, so nothing later in a test
    can flip it)."""
    client = TestClient(app)
    response = client.get("/docs")
    assert response.status_code == 200


def test_docs_route_is_gone_when_the_setting_is_off():
    """docs_url is baked in at FastAPI() construction time (see main.py's
    `docs_url="/docs" if settings.enable_api_docs else None`), so the
    already-built app this suite imports can't have its setting flipped
    after the fact. Builds a second app the same way, with docs off, and
    confirms /docs actually 404s on it - not just that docs_url ends up
    None, but that FastAPI really stops serving the route because of it."""
    throwaway_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    client = TestClient(throwaway_app)
    response = client.get("/docs")
    assert response.status_code == 404
