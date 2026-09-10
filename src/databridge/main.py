"""databridge API."""

from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, HTTPException, UploadFile
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
    fastapi_users,
    get_github_oauth_client,
)
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

app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"])
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"]
)
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


@app.post("/records/upload", response_model=IngestResult)
async def upload_records(file: UploadFile, db: Session = Depends(get_db)) -> IngestResult:
    content = await file.read()
    schema = load_schema()
    inserted, stats = ingest_file(db, file.filename or "upload.csv", content, schema)
    return IngestResult(
        rows_total=stats["rows_total"],
        rows_clean=len(inserted) - sum(1 for r in inserted if r.has_issues),
        rows_flagged=sum(1 for r in inserted if r.has_issues),
        rows_dropped_duplicates=stats["rows_dropped_duplicates"],
        records=[ClientRecordOut.model_validate(r) for r in inserted],
    )


@app.get("/records", response_model=list[ClientRecordOut])
def list_records(has_issues: bool | None = None, db: Session = Depends(get_db)) -> list:
    query = select(ClientRecord).order_by(ClientRecord.created_at.desc())
    if has_issues is not None:
        query = query.where(ClientRecord.has_issues == has_issues)
    return db.execute(query).scalars().all()


@app.get("/records/{record_id}", response_model=ClientRecordOut)
def get_record(record_id: uuid.UUID, db: Session = Depends(get_db)) -> ClientRecord:
    record = db.get(ClientRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@app.get("/records/{record_id}/webhooks", response_model=list[WebhookDeliveryOut])
def get_record_webhooks(record_id: uuid.UUID, db: Session = Depends(get_db)) -> list:
    record = db.get(ClientRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    query = select(WebhookDelivery).where(WebhookDelivery.record_id == record_id)
    return db.execute(query).scalars().all()
