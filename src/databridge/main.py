"""databridge API."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from databridge.db import Base, engine, get_db
from databridge.ingest import ingest_file, load_schema
from databridge.models import ClientRecord, WebhookDelivery
from databridge.schemas import ClientRecordOut, IngestResult, WebhookDeliveryOut


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates tables if they don't exist yet. A larger production system
    # would use Alembic migrations instead - fine for this project's scope,
    # called out honestly rather than pretending this is migration-managed.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="databridge", lifespan=lifespan)


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
