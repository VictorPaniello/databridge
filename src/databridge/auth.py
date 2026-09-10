"""Authentication: JWT (email + password) and optional GitHub OAuth.

Wires fastapi-users' pieces together - the actual HTTP routes are
registered in main.py, using the objects defined here."""

from __future__ import annotations

import logging
import re
import uuid

import httpx
from fastapi import Body, Depends, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users import exceptions as fastapi_users_exceptions
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.authentication.transport.base import (
    Transport,
    TransportLogoutNotSupportedError,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.router.oauth import (
    CSRF_TOKEN_COOKIE_NAME,
    CSRF_TOKEN_KEY,
    generate_csrf_token,
    generate_state_token,
)
from fastapi_users.schemas import BaseUser, BaseUserCreate, BaseUserUpdate
from httpx_oauth.clients.github import GitHubOAuth2
from pydantic import EmailStr, Field

from databridge.auth_db import get_user_db
from databridge.auth_models import User
from databridge.config import settings

MIN_PASSWORD_LENGTH = 8

logger = logging.getLogger(__name__)


async def _send_email(to: str, subject: str, html: str) -> None:
    """Used by UserManager.on_after_forgot_password below. None of it
    ever propagates as an exception: a failed send shouldn't turn
    forgot-password's always-succeed response (anti-enumeration) into a
    500 that reveals something differs about this particular request."""
    if not settings.resend_api_key:
        # No email provider configured (e.g. local dev) - log instead of
        # silently dropping, same as webhook_url's None-disables pattern
        # elsewhere in this file/config.py. Never acceptable left unset
        # in production, where nobody can read this log.
        logger.info("Email to %s (no provider configured): %s", to, html)
        return

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={"from": settings.email_from, "to": [to], "subject": subject, "html": html},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to send email to %s", to)


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
    # Used to sign the (separate, short-lived) tokens for password-reset
    # emails - reusing jwt_secret is fine, this is a different token
    # audience than the login JWT (fastapi-users encodes that as the
    # "aud" claim) so one being compromised doesn't hand over the other,
    # and there's no operational reason to manage a second secret.
    # verification_token_secret is required by BaseUserManager even
    # though this project doesn't send verification emails (see README's
    # "doesn't do yet" - real delivery to anyone but the Resend account
    # owner needs a verified domain this project doesn't have).
    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret

    async def create(
        self,
        user_create: UserCreate,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        """Only reached by email+password registration (fastapi-users'
        /auth/register route) - a GitHub OAuth signup never calls this,
        it creates the row directly via user_db.create() inside
        oauth_callback() with a random, nobody-knows-it password. So
        landing here always means a real, user-chosen password exists -
        has_password records that (see auth_models.py's docstring on it
        and forgot_password() below, which relies on it)."""
        user = await super().create(user_create, safe=safe, request=request)
        return await self.user_db.update(user, {"has_password": True})

    async def on_after_update(
        self, user: User, update_dict: dict, request: Request | None = None
    ) -> None:
        """update_dict is exactly what was applied - present here whether
        this came from the Settings page's PATCH /users/me (change-
        password form) or anything else that touches password through the
        same generic update path. reset_password() (below) doesn't route
        through here - it has its own dedicated on_after_reset_password
        hook, also updated to set this flag."""
        if "password" in update_dict:
            await self.user_db.update(user, {"has_password": True})

    async def forgot_password(self, user: User, request: Request | None = None) -> None:
        """Overrides the base implementation to add one more gate before
        it generates a token: an account that's never had a real,
        user-chosen password (GitHub-OAuth-only, has_password still
        False) gets no token and no email - the whole point being that a
        password can't be bootstrapped onto such an account through an
        unauthenticated email link, only explicitly from Settings while
        already signed in with GitHub. main.py's route wrapper checks
        has_password itself to tell the frontend which case this was
        (worth noting: that does mean confirming a bit more than the
        plain "202 either way" default - that the account exists *and*
        is GitHub-only - a deliberate, explicit tradeoff over silently
        letting the email path add password auth to an account nobody
        opted it into)."""
        if not user.has_password:
            return
        await super().forgot_password(user, request)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        """Called by fastapi-users' /auth/forgot-password route, and only
        when the submitted email belongs to an existing user with
        has_password already True (forgot_password() above - a
        nonexistent email, or a GitHub-only one, never reaches here or
        generates a token) - so this can never be used to create an
        account, or add password auth to one that never had it.

        `token` already embeds a fingerprint of the user's *current*
        password hash and a 1-hour expiry (fastapi-users'
        forgot_password()), so it self-invalidates the moment the
        password changes through any other path (a second reset request,
        the Settings page's own change-password form) - no extra
        single-use bookkeeping needed here."""
        reset_url = f"{settings.frontend_url}/reset-password?token={token}"
        await _send_email(
            user.email,
            "Reset your databridge password",
            "<p>Someone requested a password reset for your databridge "
            "account.</p>"
            f'<p><a href="{reset_url}">Reset your password</a></p>'
            "<p>This link expires in 1 hour and can only be used once. If "
            "you didn't request this, you can safely ignore this email - "
            "your password hasn't changed.</p>",
        )

    async def on_after_reset_password(self, user: User, request: Request | None = None) -> None:
        # Already True by the time this fires (forgot_password() above
        # only ever generates a token when it was) - set again anyway as
        # a cheap defensive backstop rather than relying on that
        # invariant never drifting.
        await self.user_db.update(user, {"has_password": True})
        logger.info("Password reset completed for %s", user.email)

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


async def forgot_password_handler(
    request: Request,
    email: EmailStr = Body(..., embed=True),
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Replaces fastapi-users' own POST /auth/forgot-password (main.py
    swaps this in the same way it swaps rate-limited endpoints and
    github_authorize_redirect onto their routers below) - same shape as
    the original (try get_by_email, swallow UserInactive, always
    succeed), but the response body also reports whether the account is
    GitHub-OAuth-only, which the library's own route has no way to
    surface. UserManager.forgot_password() (auth.py above) is what
    actually withholds the token for such an account; this only decides
    what the frontend gets told about *why* nothing arrived, and never
    generates or leaks anything the underlying flow wouldn't have.

    A nonexistent email and a GitHub-only one both still get the same
    "no email, no token" outcome underneath - only the reported
    oauth_only distinguishes them, and that's a deliberate, narrower
    tradeoff than the fully generic 202 fastapi-users ships with (see
    UserManager.forgot_password()'s docstring)."""
    try:
        user = await user_manager.get_by_email(email)
    except fastapi_users_exceptions.UserNotExists:
        return {"oauth_only": False}

    oauth_only = not user.has_password
    try:
        await user_manager.forgot_password(user, request)
    except fastapi_users_exceptions.UserInactive:
        pass
    return {"oauth_only": oauth_only}


def get_github_oauth_client() -> GitHubOAuth2 | None:
    """None when GITHUB_CLIENT_ID/SECRET aren't set - main.py skips
    registering the GitHub OAuth routes in that case, rather than
    registering a client that would fail on every request."""
    if not settings.github_client_id or not settings.github_client_secret:
        return None
    return GitHubOAuth2(settings.github_client_id, settings.github_client_secret)


def make_github_authorize_redirect(github_oauth_client: GitHubOAuth2):
    """Returns a route handler replacing fastapi-users' own GET
    /auth/github/authorize (see main.py, which swaps it in the same way it
    already swaps rate-limited endpoints onto the auth routers below) -
    reuses the exact CSRF/state generation fastapi-users' own route uses
    (imported directly from fastapi_users.router.oauth above, not
    reimplemented) but returns a real 302 to GitHub instead of a JSON
    body. Takes the already-constructed oauth client as a parameter
    (closed over below) rather than calling get_github_oauth_client()
    again, so it's guaranteed to be the exact same client instance the
    surrounding router was built with - not a second, separately
    constructed one that happens to hold the same credentials.

    Why a redirect at all: the frontend SPA lives on a different origin
    than this API. The library's default /authorize is meant to be called
    via fetch() from a SPA, which then navigates the browser to the JSON
    body's authorization_url itself - but that means the CSRF cookie this
    route sets gets set from a *cross-origin* fetch, which browsers that
    block third-party cookies by default (Chrome among them, as of when
    this was written) silently drop - discovered for real: this project's
    frontend hit OAUTH_INVALID_STATE on every attempt, `credentials:
    "include"` on the fetch included, until traced to this. Making this
    endpoint itself a redirect means the browser's own top-level
    navigation to *this* domain is what sets the cookie - first-party
    from this domain's own point of view, same as the /callback
    navigation right after it."""

    async def github_authorize_redirect(request: Request) -> RedirectResponse:
        csrf_token = generate_csrf_token()
        state = generate_state_token({CSRF_TOKEN_KEY: csrf_token}, settings.jwt_secret)
        callback_url = str(request.url_for("oauth:github.jwt-oauth-redirect.callback"))
        authorization_url = await github_oauth_client.get_authorization_url(
            callback_url, state
        )

        response = RedirectResponse(authorization_url)
        response.set_cookie(
            CSRF_TOKEN_COOKIE_NAME,
            csrf_token,
            max_age=3600,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    return github_authorize_redirect
