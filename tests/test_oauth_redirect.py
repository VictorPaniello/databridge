"""RedirectTransport (auth.py) is what makes the GitHub OAuth callback land
the browser back in the frontend SPA with a token, instead of on a bare
JSON page on the API's own origin - see its docstring for why. The GitHub
handshake itself isn't re-tested here (that needs a real GitHub account and
was verified manually via the browser during development); this exercises
the transport's own logic directly, no mocking involved since it's a small,
pure piece of code."""

from __future__ import annotations

import pytest

from databridge.auth import RedirectTransport


@pytest.mark.asyncio
async def test_get_login_response_redirects_to_frontend_with_token_in_fragment():
    # The constructor takes the full target URL, not just the frontend's
    # origin - matching how auth.py actually builds it:
    # RedirectTransport(f"{settings.frontend_url}/auth/callback").
    transport = RedirectTransport("https://example-frontend.test/auth/callback")

    response = await transport.get_login_response("a-real-looking-jwt")

    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "https://example-frontend.test/auth/callback#access_token=a-real-looking-jwt"
    )


@pytest.mark.asyncio
async def test_get_login_response_does_not_put_the_token_in_a_query_string():
    """The token must land in the URL *fragment*, not a query string -
    fragments are never sent to any server (this one included, on any
    subsequent navigation) or written to server access logs; a query
    string would be both."""
    transport = RedirectTransport("https://example-frontend.test/auth/callback")

    response = await transport.get_login_response("secret-token-value")

    location = response.headers["location"]
    assert "?" not in location
    assert location.split("#", 1)[1] == "access_token=secret-token-value"
