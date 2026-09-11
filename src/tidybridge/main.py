"""tidybridge API."""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

# Registers users/oauth_account on Base.metadata - not used directly here,
# but tests' create_all() (see conftest.py) needs every table module
# imported somewhere in this chain to know about them.
import tidybridge.auth_models  # noqa: F401
from tidybridge.auth import (
    UserCreate,
    UserRead,
    UserUpdate,
    auth_backend,
    current_active_user,
    fastapi_users,
    forgot_password_handler,
    get_github_oauth_client,
    make_github_authorize_redirect,
    oauth_redirect_backend,
)
from tidybridge.auth_models import User
from tidybridge.config import settings
from tidybridge.db import get_db
from tidybridge.ingest import ingest_file, load_schema
from tidybridge.models import ClientRecord, IngestionRun, WebhookDelivery, WebhookJob
from tidybridge.schemas import (
    ClientRecordOut,
    IngestionRunOut,
    IngestionRunsPage,
    IngestResult,
    RecordsPage,
    WebhookDeliveryOut,
    WebhookJobStatusOut,
)
from tidybridge.webhooks import notify_new_record

# Nothing else in this process configures logging - Python's root logger
# defaults to WARNING with zero handlers attached, so a plain
# logger.info(...) anywhere under the "tidybridge" namespace (auth.py's
# password-reset logging, notably) would be silently discarded at the
# effective-level check before it ever reached output, in both `uvicorn
# --reload` locally and the real Dockerfile CMD on Railway - found by
# actually looking for the logged reset link during manual testing and
# finding nothing, not by inspecting this in isolation. uvicorn's own
# dictConfig (uvicorn.config.LOGGING_CONFIG) only wires up its own
# "uvicorn"/"uvicorn.access" loggers, so this app's own logger needs its
# own explicit level + handler; propagate=False keeps it from also
# duplicating through root if root ever gets a handler configured later.
_tidybridge_logger = logging.getLogger("tidybridge")
_tidybridge_logger.setLevel(logging.INFO)
_tidybridge_logger.addHandler(logging.StreamHandler())
_tidybridge_logger.propagate = False

# Schema is Alembic-managed now (see alembic/), not created on startup -
# `alembic upgrade head` runs before the app starts (Dockerfile's CMD;
# locally, run it by hand once after pulling schema changes). The previous
# Base.metadata.create_all() on every startup only ever created missing
# tables, never altered existing ones - real bugs found once a column
# needed adding to an already-deployed table (see CHANGELOG).
#
# docs_url/redoc_url/openapi_url are None in production (settings.
# enable_api_docs=False there) - see its own docstring in config.py for
# why: not a security boundary, just no reason to leave the whole API
# schema publicly browsable once this is actually live.
app = FastAPI(
    title="tidybridge",
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

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
    # Content-Disposition isn't on the browser's default CORS-safelisted
    # response headers - without exposing it explicitly, the frontend's
    # fetch() for GET /records/export can't read the filename the backend
    # generated (see api/client.ts's exportRecords()), even though the
    # response itself comes through fine either way.
    expose_headers=["Content-Disposition"],
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


# Defense-in-depth response headers - neither FastAPI/Starlette nor
# anything else in front of this API (Railway passes requests straight
# through, no CDN/reverse-proxy config of its own) add any of these by
# default. Registered last (Starlette's middleware stack runs the
# most-recently-added one outermost), so it still applies to CORS
# preflight responses and SlowAPI's own 429s, not just routes that reach
# a normal handler.
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Harmless to send over plain HTTP too (browsers only ever honor it on
    # an HTTPS response) - Railway terminates TLS in front of this app in
    # production, see the README's Deployment section.
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


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

# Registered *before* the generic users router below, and matched on the
# literal path "/users/me" - fastapi-users' own router already has a
# DELETE /users/{id}, but it's superuser-only (an admin deleting some
# other account by id), not a self-service route for a caller to delete
# their own. Registration order matters here: FastAPI/Starlette matches
# routes in the order they were added, so this literal "/me" path must
# be registered first or a parameterized "/{id}" route from the router
# below would shadow it and try (and fail) to parse "me" as a user id -
# the exact class of routing-shadow bug this project has hit before with
# PATCH /users/me (see CHANGELOG/README's Bugs section).
@app.delete("/users/me", status_code=204)
def delete_own_account(
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> None:
    """Permanently erase the caller's own account and everything tied to
    it - every client record, ingestion run, and (via client_records'
    own further cascade) webhook delivery they own, plus any linked
    GitHub OAuth account. Real deletion via ON DELETE CASCADE at the
    database level (migration e986a7123298), the same principle
    DELETE /records/{id} already uses - not a soft-delete flag some
    other query could still surface. The bearer token the caller
    authenticated with keeps its own signature valid (JWTs are
    stateless - there's no server-side session to revoke), but the very
    next request made with it 401s the moment current_active_user tries
    to look the now-deleted user id back up, so it self-invalidates on
    first use after deletion rather than staying usable.
    """
    db.execute(delete(User).where(User.id == user.id))
    db.commit()


app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"]
)

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
    user: User = Depends(current_active_user),
) -> IngestResult:
    content = await _read_upload_within_limit(file)
    schema = load_schema()
    # ingest_file does CPU-bound CSV parsing and several synchronous DB
    # round-trips (schema mapping/validation via tidycsv, one INSERT per
    # row) - no longer a blocking httpx.post to the webhook receiver too,
    # since the automatic post-ingest notification is now enqueue_delivery()
    # (a fast DB insert, see webhooks.py/webhook_worker.py) instead of a
    # direct notify_new_record() call, but the CSV/DB work alone still
    # justifies offloading. This route is `async def` (needed for `await
    # file.read()` above), and FastAPI only auto-offloads *sync* `def`
    # routes to a worker thread - a sync call made directly inside an
    # async route runs straight on the single event loop thread instead,
    # stalling every other in-flight request for as long as it takes.
    # run_in_threadpool moves it off the loop, the same mechanism FastAPI
    # itself uses for sync routes. Found via a deliberate scalability/
    # performance review, not a user report.
    inserted, run = await run_in_threadpool(
        ingest_file, db, file.filename or "upload.csv", content, schema, user.id
    )
    return IngestResult(
        ingestion_run_id=run.id,
        rows_total=run.rows_total,
        rows_clean=run.rows_clean,
        rows_flagged=run.rows_flagged,
        rows_dropped_duplicates=run.rows_dropped_duplicates,
        rows_skipped_existing=run.rows_skipped_existing,
        records=[ClientRecordOut.model_validate(r) for r in inserted],
    )


@app.get("/ingestion-runs", response_model=IngestionRunsPage)
def list_ingestion_runs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> IngestionRunsPage:
    base_query = select(IngestionRun).where(IngestionRun.owner_id == user.id)
    total = db.execute(select(func.count()).select_from(base_query.subquery())).scalar_one()

    page_query = base_query.order_by(IngestionRun.created_at.desc()).limit(limit).offset(offset)
    runs = db.execute(page_query).scalars().all()

    return IngestionRunsPage(
        items=[IngestionRunOut.model_validate(r) for r in runs],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/ingestion-runs/{run_id}", response_model=IngestionRunOut)
def get_ingestion_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> IngestionRun:
    run = db.get(IngestionRun, run_id)
    if run is None or run.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Ingestion run not found")
    return run


@app.get("/records", response_model=RecordsPage)
def list_records(
    has_issues: bool | None = None,
    # Lets a caller go from "this run had 3 flagged rows" (an
    # IngestionRun) to "show me exactly those rows" - no separate
    # ownership check needed here beyond the owner_id filter already
    # below: passing another engineer's run_id just matches zero of
    # *this* caller's records, never leaks anyone else's.
    ingestion_run_id: uuid.UUID | None = None,
    # 500 is a hard ceiling regardless of what a caller asks for, not just
    # a default - previously this endpoint had no limit at all, so a
    # client with (say) 50,000 records made one query and one response
    # body pull every row in at once. 100 is the default page size for a
    # caller that doesn't ask for a specific one.
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> RecordsPage:
    base_query = select(ClientRecord).where(ClientRecord.owner_id == user.id)
    if has_issues is not None:
        base_query = base_query.where(ClientRecord.has_issues == has_issues)
    if ingestion_run_id is not None:
        base_query = base_query.where(ClientRecord.ingestion_run_id == ingestion_run_id)

    # Counted against the same filtered base_query (not a second,
    # separately-filtered one) so total always matches what has_issues
    # actually scoped the page to, not the caller's total record count.
    total = db.execute(select(func.count()).select_from(base_query.subquery())).scalar_one()

    page_query = base_query.order_by(ClientRecord.created_at.desc()).limit(limit).offset(offset)
    records = db.execute(page_query).scalars().all()

    return RecordsPage(
        items=[ClientRecordOut.model_validate(r) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


def _format_issues(issues: list[dict] | None) -> str:
    """One readable line per validation issue - "field: issue; field:
    issue" - rather than the raw JSON GET /records/{id} returns, since
    this is meant to be opened directly in a spreadsheet."""
    if not issues:
        return ""
    return "; ".join(f"{issue['field']}: {issue['issue']}" for issue in issues)


# Registered before GET /records/{record_id} - the literal "export" path
# would otherwise be shadowed by {record_id} trying (and failing) to
# parse it as a UUID, the exact class of routing-shadow bug this project
# has hit before (see README's Bugs section, and DELETE /users/me above).
@app.get("/records/export")
def export_records(
    ingestion_run_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> Response:
    """A CSV download of every one of the caller's own records matching
    the filter - not the paginated JSON GET /records returns, since the
    whole point of exporting is getting everything out in one file, not
    a page at a time. Same ownership/filter semantics as GET /records
    (see its own comments) - just unpaginated and shaped as CSV instead
    of JSON."""
    base_query = select(ClientRecord).where(ClientRecord.owner_id == user.id)
    if ingestion_run_id is not None:
        base_query = base_query.where(ClientRecord.ingestion_run_id == ingestion_run_id)
    records = db.execute(base_query.order_by(ClientRecord.created_at.desc())).scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["full_name", "email", "signup_date", "amount", "phone", "has_issues", "issues"]
    )
    for record in records:
        writer.writerow(
            [
                record.full_name,
                record.email,
                record.signup_date,
                record.amount,
                record.phone,
                record.has_issues,
                _format_issues(record.issues),
            ]
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"tidybridge-records-{timestamp}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    user: User = Depends(current_active_user),
) -> ClientRecord:
    return _get_owned_record(db, record_id, user)


@app.get("/records/{record_id}/webhooks", response_model=list[WebhookDeliveryOut])
def get_record_webhooks(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> list:
    _get_owned_record(db, record_id, user)
    # Ordered explicitly - now that a single delivery can produce several
    # rows (one per retry attempt, see webhooks.py), an unordered query
    # no longer reliably reads as "the retry history in order it
    # happened" the way a single-row-per-delivery result always did.
    query = (
        select(WebhookDelivery)
        .where(WebhookDelivery.record_id == record_id)
        .order_by(WebhookDelivery.attempted_at, WebhookDelivery.id)
    )
    return db.execute(query).scalars().all()


@app.get("/records/{record_id}/webhook-status", response_model=WebhookJobStatusOut)
def get_record_webhook_status(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> WebhookJobStatusOut:
    """The automatic post-ingest delivery pipeline's current state for one
    record - see WebhookJobStatusOut's docstring for what each status
    means."""
    _get_owned_record(db, record_id, user)
    job_query = select(WebhookJob).where(WebhookJob.record_id == record_id)
    job = db.execute(job_query).scalar_one_or_none()
    if job is None:
        return WebhookJobStatusOut(status="not_configured", attempt_number=None, available_at=None)
    return WebhookJobStatusOut(
        status=job.status, attempt_number=job.attempt_number, available_at=job.available_at
    )


@app.post("/records/{record_id}/webhooks/replay", response_model=WebhookDeliveryOut)
def replay_webhook(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
) -> WebhookDelivery:
    """Manually re-sends the notification for one record, on demand - a
    real, separate action from the automatic retries in webhooks.py, not
    another one of them. This is what a human actually does mid-incident:
    a client says "our endpoint was down, we just fixed it, please resend
    the last few" - waiting for an automatic retry schedule that already
    exhausted itself doesn't help at that point.

    Replays the record's *current* payload, not a stored historical one -
    webhooks.py never persisted the literal bytes of a past attempt, only
    its outcome (status_code/success/error), so there is no historical
    payload to resend verbatim. In practice this is the same payload
    every time regardless: nothing in this API ever mutates a
    ClientRecord's fields after ingest, only deletes it outright, so
    "current" and "at first delivery" are the same data.

    Goes through the exact same signing/retry path as the original
    delivery (same settings.webhook_max_attempts, same backoff) - a
    replay that hits another transient failure retries the same way an
    original delivery would, rather than failing after one try.
    """
    record = _get_owned_record(db, record_id, user)
    delivery = notify_new_record(db, record)
    if delivery is None:
        raise HTTPException(status_code=400, detail="No webhook URL is configured")
    return delivery


@app.delete("/records/{record_id}", status_code=204)
def delete_record(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_active_user),
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
