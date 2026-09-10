"""User identity tables.

Kept separate from models.py (which holds databridge's own domain
tables) because these are fastapi-users' tables - id, email, hashed
password, active/verified flags, and linked OAuth accounts (e.g. GitHub)
are all managed by that library, not hand-rolled here."""

from __future__ import annotations

import uuid

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from databridge.db import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    """One row per (user, OAuth provider) - e.g. a user who signed in with
    GitHub gets one row here linking their User to their GitHub account id.
    A user could in principle link more than one provider to the same
    account, which is why this is its own table rather than columns on
    User."""

    # fastapi-users' mixin hardcodes this FK to ForeignKey("user.id") -
    # singular, matching its own examples. Every other table in this
    # project is plural (client_records, webhook_deliveries), so User
    # below keeps __tablename__ = "users" for consistency and this column
    # is redeclared to point at the right table instead.
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="cascade"), nullable=False
    )


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    oauth_accounts: Mapped[list[OAuthAccount]] = relationship("OAuthAccount", lazy="joined")
