"""Async database session, used only by the authentication subsystem.

fastapi-users' SQLAlchemyUserDatabase requires an async SQLAlchemy
session - the rest of databridge (db.py) uses a synchronous engine/
session instead. Rather than rewriting the whole service to async just
for this, a second engine lives here, pointed at the same database.
psycopg3 (already a dependency, for the sync engine) supports async
natively through the same "postgresql+psycopg" driver name - no extra
async driver package needed, and no separate connection string."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from databridge.auth_models import OAuthAccount, User
from databridge.config import settings

async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)
