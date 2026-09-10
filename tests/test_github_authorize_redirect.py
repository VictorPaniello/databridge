"""github_authorize_redirect (auth.py) replaces fastapi-users' own GET
/auth/github/authorize - see its docstring for why (the default JSON
response's CSRF cookie gets dropped by browsers that block third-party
cookies, since the frontend SPA and this API are different origins).

Exercises the real route through a real (throwaway) FastAPI app and a real
GitHubOAuth2 client - fake credentials are fine because /authorize never
calls GitHub's API itself, it only builds a URL and sets a cookie. No
network access, no mocking of fastapi-users' own CSRF/state logic."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx_oauth.clients.github import GitHubOAuth2

from databridge.auth import fastapi_users, make_github_authorize_redirect, oauth_redirect_backend
from databridge.config import settings


def _build_test_app() -> FastAPI:
    fake_client = GitHubOAuth2("fake-client-id", "fake-client-secret")
    router = fastapi_users.get_oauth_router(
        fake_client, oauth_redirect_backend, settings.jwt_secret, associate_by_email=True
    )
    for route in router.routes:
        if route.path == "/authorize":
            route.endpoint = make_github_authorize_redirect(fake_client)

    app = FastAPI()
    app.include_router(router, prefix="/auth/github")
    return app


def test_authorize_redirects_straight_to_github():
    client = TestClient(_build_test_app(), base_url="https://testserver")
    response = client.get("/auth/github/authorize", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://github.com/login/oauth/authorize")


def test_authorize_sets_the_csrf_cookie_fastapi_users_expects():
    client = TestClient(_build_test_app(), base_url="https://testserver")
    response = client.get("/auth/github/authorize", follow_redirects=False)

    assert "fastapiusersoauthcsrf" in response.cookies
    # Regenerated per request, not a fixed value - a real CSRF token, not
    # a placeholder.
    token = response.cookies["fastapiusersoauthcsrf"]
    assert len(token) > 20


def test_authorize_encodes_the_same_csrf_token_into_the_state_param():
    """The whole point: the cookie's value and the state param's embedded
    csrf token must match, since /callback compares them. Decodes the
    state JWT the same way fastapi-users' own callback route does."""
    from fastapi_users.router.oauth import CSRF_TOKEN_KEY, STATE_TOKEN_AUDIENCE, decode_jwt

    client = TestClient(_build_test_app(), base_url="https://testserver")
    response = client.get("/auth/github/authorize", follow_redirects=False)

    cookie_csrf = response.cookies["fastapiusersoauthcsrf"]
    location = response.headers["location"]
    state_param = next(
        part.split("=", 1)[1] for part in location.split("&") if part.startswith("state=")
    )
    # The URL-encoded state param needs decoding before it's a valid JWT.
    from urllib.parse import unquote

    state_data = decode_jwt(unquote(state_param), settings.jwt_secret, [STATE_TOKEN_AUDIENCE])
    assert state_data[CSRF_TOKEN_KEY] == cookie_csrf
