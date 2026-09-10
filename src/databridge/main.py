"""databridge API."""

from __future__ import annotations

import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi_users import exceptions as fastapi_users_exceptions
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

# Registers users/oauth_account on Base.metadata - not used directly here,
# but tests' create_all() (see conftest.py) needs every table module
# imported somewhere in this chain to know about them.
import databridge.auth_models  # noqa: F401
from databridge.auth import (
    UserCreate,
    UserManager,
    UserRead,
    UserUpdate,
    auth_backend,
    current_verified_active_user,
    fastapi_users,
    forgot_password_handler,
    get_github_oauth_client,
    get_user_manager,
    make_github_authorize_redirect,
    oauth_redirect_backend,
)
from databridge.auth_models import User
from databridge.config import settings
from databridge.db import get_db
from databridge.ingest import ingest_file, load_schema
from databridge.models import ClientRecord, WebhookDelivery
from databridge.schemas import ClientRecordOut, IngestResult, WebhookDeliveryOut

# Nothing else in this process configures logging - Python's root logger
# defaults to WARNING with zero handlers attached, so a plain
# logger.info(...) anywhere under the "databridge" namespace (auth.py's
# password-reset logging, notably) would be silently discarded at the
# effective-level check before it ever reached output, in both `uvicorn
# --reload` locally and the real Dockerfile CMD on Railway - found by
# actually looking for the logged reset link during manual testing and
# finding nothing, not by inspecting this in isolation. uvicorn's own
# dictConfig (uvicorn.config.LOGGING_CONFIG) only wires up its own
# "uvicorn"/"uvicorn.access" loggers, so this app's own logger needs its
# own explicit level + handler; propagate=False keeps it from also
# duplicating through root if root ever gets a handler configured later.
_databridge_logger = logging.getLogger("databridge")
_databridge_logger.setLevel(logging.INFO)
_databridge_logger.addHandler(logging.StreamHandler())
_databridge_logger.propagate = False

# Schema is Alembic-managed now (see alembic/), not created on startup -
# `alembic upgrade head` runs before the app starts (Dockerfile's CMD;
# locally, run it by hand once after pulling schema changes). The previous
# Base.metadata.create_all() on every startup only ever created missing
# tables, never altered existing ones - real bugs found once a column
# needed adding to an already-deployed table (see CHANGELOG).
app = FastAPI(title="databridge")

# Only the configured frontend origin may call this API from a browser -
# not "*", since credentialed requests (the Authorization header the SPA
# sends on every authenticated call) are never allowed with a wildcard
# origin anyway, and there's exactly one legitimate frontend for this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting: a generous default across the whole API as a general flood
# safety net, with a much stricter limit specifically on /login and
# /register - the two endpoints a brute-force or credential-stuffing
# attempt would actually hammer. Keyed on the caller's IP; this relies on
# get_remote_address reading the real client IP from what Railway's proxy
# forwards, not uvicorn's own socket peer (see the Dockerfile's
# --proxy-headers) - otherwise every request behind that proxy would share
# one bucket and one abusive caller could rate-limit every legitimate one.
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_STRICT_AUTH_LIMIT = "5/minute"

_jwt_router = fastapi_users.get_auth_router(auth_backend)
for _route in _jwt_router.routes:
    if _route.path == "/login":
        _route.endpoint = limiter.limit(_STRICT_AUTH_LIMIT)(_route.endpoint)
app.include_router(_jwt_router, prefix="/auth/jwt", tags=["auth"])

_register_router = fastapi_users.get_register_router(UserRead, UserCreate)
for _route in _register_router.routes:
    if _route.path == "/register":
        _route.endpoint = limiter.limit(_STRICT_AUTH_LIMIT)(_route.endpoint)
app.include_router(_register_router, prefix="/auth", tags=["auth"])

# Both routes get the strict limit: /forgot-password because it's the
# enumeration/spam-mail surface (an attacker hammering it either floods a
# victim's inbox or - if the response ever timed differently - could probe
# which emails are registered), /reset-password because it's a brute-force
# surface against the token itself, same threat model as /login above.
_reset_router = fastapi_users.get_reset_password_router()
for _route in _reset_router.routes:
    if _route.path == "/forgot-password":
        # Swapped for forgot_password_handler (auth.py) the same way
        # /authorize is swapped onto the GitHub router below - the
        # library's own endpoint can't tell the frontend a GitHub-only
        # account was refused a reset token (see UserManager.
        # forgot_password()'s docstring for why it's refused at all).
        # The route's own decorator-configured status_code (202) still
        # applies - only the endpoint function underneath is replaced.
        _route.endpoint = limiter.limit(_STRICT_AUTH_LIMIT)(forgot_password_handler)
    elif _route.path == "/reset-password":
        _route.endpoint = limiter.limit(_STRICT_AUTH_LIMIT)(_route.endpoint)
app.include_router(_reset_router, prefix="/auth", tags=["auth"])

# Same strict limit again: /request-verify-token is a spam-mail surface
# (an attacker hammering it floods a victim's inbox) the same way
# /forgot-password is, /verify a brute-force surface against the token
# itself the same way /reset-password is.
_verify_router = fastapi_users.get_verify_router(UserRead)
for _route in _verify_router.routes:
    if _route.path in ("/request-verify-token", "/verify"):
        _route.endpoint = limiter.limit(_STRICT_AUTH_LIMIT)(_route.endpoint)
app.include_router(_verify_router, prefix="/auth", tags=["auth"])

# fastapi-users' get_users_router() has no way to require verification on
# PATCH /me while leaving GET /me open - both share one
# requires_verification flag, and GET /me has to stay reachable while
# unverified regardless (AuthContext's refreshUser() calls it on every
# load to learn is_verified in the first place - gating it too would
# make it impossible for the frontend to ever tell "signed in but
# unverified" apart from "not signed in", since both would just 401/403).
# So: keep only the library's GET /me, and register a replacement PATCH
# /me below requiring current_verified_active_user - no exceptions, not
# even to fix a typo'd email before it's verified.
#
# This app never uses the library's admin-style /{id} routes (all
# superuser-gated; nothing here ever sets a superuser) - they're dropped
# too, not just left unused. Found the hard way why that matters: dropping
# only "/me" PATCH and leaving "/{id}" PATCH registered meant PATCH
# /users/me got silently caught by "/{id}" with id="me" instead of ever
# reaching the new route below, since it was the only remaining PATCH
# route in this sub-router - a superuser check that fails for literally
# everyone, worded identically to a verification failure (both a bare 403
# Forbidden), so it looked at first like verification itself was broken.
_users_router = fastapi_users.get_users_router(UserRead, UserUpdate)
_users_router.routes = [
    _route for _route in _users_router.routes if _route.path == "/me" and "GET" in _route.methods
]
app.include_router(_users_router, prefix="/users", tags=["users"])


@app.patch("/users/me", response_model=UserRead)
async def patch_me(
    user_update: UserUpdate,
    request: Request,
    user: User = Depends(current_verified_active_user),
    user_manager: UserManager = Depends(get_user_manager),
) -> User:
    try:
        return await user_manager.update(user_update, user, safe=True, request=request)
    except fastapi_users_exceptions.InvalidPasswordException as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "UPDATE_USER_INVALID_PASSWORD", "reason": e.reason},
        ) from e
    except fastapi_users_exceptions.UserAlreadyExists as e:
        raise HTTPException(status_code=400, detail="UPDATE_USER_EMAIL_ALREADY_EXISTS") from e

_github_oauth_client = get_github_oauth_client()
if _github_oauth_client is not None:
    _github_router = fastapi_users.get_oauth_router(
        _github_oauth_client,
        # oauth_redirect_backend, not auth_backend: the callback ends
        # with a 302 to the frontend carrying the JWT in the URL
        # fragment (see auth.py's RedirectTransport), instead of a bare
        # JSON body on the API's own origin - regular email+password
        # login is untouched, still bearer_transport/auth_backend.
        oauth_redirect_backend,
        settings.jwt_secret,
        # A user who registered with email+password and later signs in
        # with GitHub using the same email gets linked to that same
        # account instead of silently creating a second one.
        associate_by_email=True,
        # GitHub already confirmed this email address on its end (and the
        # user had to actually be signed into that GitHub account to get
        # here) - no reason to make them click a second verification
        # email databridge would send. UserManager.on_after_register
        # (auth.py) still calls request_verify() unconditionally for
        # every new signup, GitHub included; it's this flag that makes
        # that a no-op here by making is_verified already True when it's
        # called.
        is_verified_by_default=True,
    )
    # /authorize's own default response is JSON (meant to be fetch()'d by
    # a SPA), which breaks the CSRF cookie it sets under third-party-
    # cookie-blocking browsers when the SPA is on a different origin -
    # see github_authorize_redirect's docstring. Swapped the same way
    # rate limiting is swapped onto /login and /register above: mutating
    # the sub-router's route before include_router() re-derives the final
    # route (dependant included) from the mutated endpoint.
    for _route in _github_router.routes:
        if _route.path == "/authorize":
            _route.endpoint = make_github_authorize_redirect(_github_oauth_client)
    app.include_router(_github_router, prefix="/auth/github", tags=["auth"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


_MAX_UPLOAD_BYTES = settings.max_upload_size_mb * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_upload_within_limit(file: UploadFile) -> bytes:
    """Reads in bounded chunks and aborts as soon as the limit is crossed,
    rather than trusting the Content-Length header (a client can send
    whatever it wants there) or reading the whole body before checking."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {settings.max_upload_size_mb} MB upload limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/records/upload", response_model=IngestResult)
async def upload_records(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(current_verified_active_user),
) -> IngestResult:
    content = await _read_upload_within_limit(file)
    schema = load_schema()
    # ingest_file does CPU-bound CSV parsing, several synchronous DB
    # round-trips, and (via notify_new_record) a blocking httpx.post to the
    # webhook receiver with up to a 5s timeout. This route is `async def`
    # (needed for `await file.read()` above), and FastAPI only auto-offloads
    # *sync* `def` routes to a worker thread - a sync call made directly
    # inside an async route runs straight on the single event loop thread
    # instead, stalling every other in-flight request for as long as it
    # takes. run_in_threadpool moves it off the loop, the same mechanism
    # FastAPI itself uses for sync routes. Found via a deliberate
    # scalability/performance review, not a user report.
    inserted, stats = await run_in_threadpool(
        ingest_file, db, file.filename or "upload.csv", content, schema, user.id
    )
    return IngestResult(
        rows_total=stats["rows_total"],
        rows_clean=len(inserted) - sum(1 for r in inserted if r.has_issues),
        rows_flagged=sum(1 for r in inserted if r.has_issues),
        rows_dropped_duplicates=stats["rows_dropped_duplicates"],
        records=[ClientRecordOut.model_validate(r) for r in inserted],
    )


@app.get("/records", response_model=list[ClientRecordOut])
def list_records(
    has_issues: bool | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_verified_active_user),
) -> list:
    query = (
        select(ClientRecord)
        .where(ClientRecord.owner_id == user.id)
        .order_by(ClientRecord.created_at.desc())
    )
    if has_issues is not None:
        query = query.where(ClientRecord.has_issues == has_issues)
    return db.execute(query).scalars().all()


def _get_owned_record(db: Session, record_id: uuid.UUID, user: User) -> ClientRecord:
    """404, not 403, when the record belongs to someone else - existence of
    another engineer's client record shouldn't be observable at all."""
    record = db.get(ClientRecord, record_id)
    if record is None or record.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@app.get("/records/{record_id}", response_model=ClientRecordOut)
def get_record(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_verified_active_user),
) -> ClientRecord:
    return _get_owned_record(db, record_id, user)


@app.get("/records/{record_id}/webhooks", response_model=list[WebhookDeliveryOut])
def get_record_webhooks(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_verified_active_user),
) -> list:
    _get_owned_record(db, record_id, user)
    query = select(WebhookDelivery).where(WebhookDelivery.record_id == record_id)
    return db.execute(query).scalars().all()


@app.delete("/records/{record_id}", status_code=204)
def delete_record(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_verified_active_user),
) -> None:
    """Real deletion, not a soft-delete flag - the GDPR right-to-erasure
    case this exists for means the data actually has to stop existing, not
    just stop being shown. The FK's ON DELETE CASCADE (see models.py) takes
    the record's webhook_deliveries audit trail with it - keeping delivery
    logs that still carry the erased record's id around would defeat the
    point."""
    record = _get_owned_record(db, record_id, user)
    db.delete(record)
    db.commit()
