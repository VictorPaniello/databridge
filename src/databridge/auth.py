"""Authentication: JWT (email + password) and optional GitHub OAuth.

Wires fastapi-users' pieces together - the actual HTTP routes are
registered in main.py, using the objects defined here."""

from __future__ import annotations

import re
import uuid

from fastapi import Depends, Response
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.authentication.transport.base import (
    Transport,
    TransportLogoutNotSupportedError,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUser, BaseUserCreate, BaseUserUpdate
from httpx_oauth.clients.github import GitHubOAuth2
from pydantic import Field

from databridge.auth_db import get_user_db
from databridge.auth_models import User
from databridge.config import settings

MIN_PASSWORD_LENGTH = 8


class UserRead(BaseUser[uuid.UUID]):
    # None for any user who never went through UserCreate below - notably
    # every GitHub OAuth signup, since fastapi-users' oauth_callback
    # creates the user directly and never touches UserCreate/validate_
    # password's sibling validation. The frontend's greeting falls back to
    # the email in that case rather than assuming this is always set.
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


class UserCreate(BaseUserCreate):
    # Required for email+password registration (this drives the frontend's
    # "Hola, {first_name}{last_name[0]}" greeting) - phone stays optional,
    # nothing in this project actually needs it yet.
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)


class UserUpdate(BaseUserUpdate):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    # Used to sign the (separate, short-lived) tokens for password-reset and
    # email-verification emails - reusing jwt_secret is fine since this
    # project doesn't send those emails yet (see README's "doesn't do yet").
    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret

    async def validate_password(self, password: str, user: UserCreate | User) -> None:
        """fastapi-users applies no strength requirement by default (a
        one-character password was accepted before this override - found
        in a security review). Overriding this is the documented extension
        point, called on both registration and password change."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidPasswordException(
                reason=f"Password must be at least {MIN_PASSWORD_LENGTH} characters long"
            )
        if not re.search(r"[A-Z]", password):
            raise InvalidPasswordException(
                reason="Password must contain at least one uppercase letter"
            )
        if not re.search(r"[a-z]", password):
            raise InvalidPasswordException(
                reason="Password must contain at least one lowercase letter"
            )
        if not re.search(r"[0-9]", password):
            raise InvalidPasswordException(reason="Password must contain at least one digit")
        if not re.search(r"[^A-Za-z0-9]", password):
            raise InvalidPasswordException(
                reason="Password must contain at least one special character"
            )


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> UserManager:
    return UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.jwt_secret, lifetime_seconds=3600 * 24 * 7)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


class RedirectTransport(Transport):
    """Used only for the GitHub OAuth callback, never for regular
    email+password login (that stays on bearer_transport/auth_backend,
    unchanged). fastapi-users' oauth router always ends by calling
    `backend.login(strategy, user)` and returning whatever Response that
    gives back - with BearerTransport that's a raw JSON body, which would
    leave a browser sitting on an ugly JSON page on the API's own origin
    after GitHub redirects it to /auth/github/callback, instead of back in
    the SPA. A custom Transport is fastapi-users' own supported extension
    point for changing that response shape (same Protocol BearerTransport
    and CookieTransport implement) - not a bypass of its auth/CSRF logic,
    which is untouched.

    The token goes in the URL fragment (`#access_token=...`), not a query
    string: fragments are never sent to the server in the request line or
    logged by it, and the frontend's callback route reads it client-side
    with `window.location.hash` and clears it immediately after."""

    scheme = OAuth2PasswordBearer(tokenUrl="auth/jwt/login", auto_error=False)

    def __init__(self, redirect_url: str):
        self.redirect_url = redirect_url

    async def get_login_response(self, token: str) -> Response:
        return RedirectResponse(f"{self.redirect_url}#access_token={token}", status_code=302)

    async def get_logout_response(self) -> Response:
        raise TransportLogoutNotSupportedError()

    @staticmethod
    def get_openapi_login_responses_success() -> dict:
        return {}

    @staticmethod
    def get_openapi_logout_responses_success() -> dict:
        return {}


oauth_redirect_backend = AuthenticationBackend(
    name="jwt-oauth-redirect",
    transport=RedirectTransport(f"{settings.frontend_url}/auth/callback"),
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
# Doesn't 401 on a missing/invalid token - returns None instead. Used where
# an endpoint should still work for anyone, but personalize its response
# for a signed-in engineer (none of databridge's endpoints use this yet).
current_active_user_optional = fastapi_users.current_user(active=True, optional=True)


def get_github_oauth_client() -> GitHubOAuth2 | None:
    """None when GITHUB_CLIENT_ID/SECRET aren't set - main.py skips
    registering the GitHub OAuth routes in that case, rather than
    registering a client that would fail on every request."""
    if not settings.github_client_id or not settings.github_client_secret:
        return None
    return GitHubOAuth2(settings.github_client_id, settings.github_client_secret)
