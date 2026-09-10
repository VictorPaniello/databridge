"""Test fixtures. Tests run against a real PostgreSQL database
(databridge_test), not a mock or an in-memory substitute - the whole point
of this project is proving the ingest -> Postgres -> API path actually
works, and an in-memory fake DB would silently hide anything SQLAlchemy or
Postgres itself does differently from an assumption baked into a mock."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/databridge_test"
)
os.environ.setdefault("WEBHOOK_URL", "")  # no webhook during tests - keep them hermetic

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from databridge.db import Base, SessionLocal, engine, get_db
from databridge.main import app


@pytest.fixture(autouse=True)
def _clean_tables():
    Base.metadata.create_all(bind=engine)
    yield
    with engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE webhook_deliveries, client_records")


@pytest.fixture
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
