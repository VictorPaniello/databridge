"""databridge API."""

from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

# Registers users/oauth_account on Base.metadata - not used directly here,
# but tests' create_all() (see conftest.py) needs every table module
# imported somewhere in this chain to know about them.
import databridge.auth_models  # noqa: F401
from databridge.auth import (
    UserCreate,
    UserRead,
    UserUpdate,
    auth_backend,
    current_active_user,
    fastapi_users,
    get_github_oauth_client,
)
from databridge.auth_models import User
from databridge.config import settings
from databridge.db import get_db
from databridge.ingest import ingest_file, load_schema
from databridge.models import ClientRecord, WebhookDelivery
from databridge.schemas import ClientRecordOut, IngestResult, WebhookDeliveryOut

# Schema is Alembic-managed now (see alembic/), not created on startup -
# `alembic upgrade head` runs before the app starts (Dockerfile's CMD;
# locally, run it by hand once after pulling schema changes). The previous
# Base.metadata.create_all() on every startup only ever created missing
# tables, never altered existing ones - real bugs found once a column
# needed adding to an already-deployed table (see CHANGELOG).
app = FastAPI(title="databridge")

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
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"]
)

_github_oauth_client = get_github_oauth_client()
if _github_oauth_client is not None:
    app.include_router(
        fastapi_users.get_oauth_router(
            _github_oauth_client,
            auth_backend,
            settings.jwt_secret,
            # A user who registered with email+password and later signs in
            # with GitHub using the same email gets linked to that same
            # account instead of silently creating a second one.
            associate_by_email=True,
        ),
        prefix="/auth/github",
        tags=["auth"],
    )


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
    inserted, stats = ingest_file(db, file.filename or "upload.csv", content, schema, user.id)
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
    user: User = Depends(current_active_user),
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
    query = select(WebhookDelivery).where(WebhookDelivery.record_id == record_id)
    return db.execute(query).scalars().all()
