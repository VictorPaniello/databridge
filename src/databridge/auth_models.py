"""User identity tables.

Kept separate from models.py (which holds databridge's own domain
tables) because these are fastapi-users' tables - id, email, hashed
password, active/verified flags, and linked OAuth accounts (e.g. GitHub)
are all managed by that library, not hand-rolled here."""

from __future__ import annotations

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
from sqlalchemy.orm import Mapped, relationship

from databridge.db import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    """One row per (user, OAuth provider) - e.g. a user who signed in with
    GitHub gets one row here linking their User to their GitHub account id.
    A user could in principle link more than one provider to the same
    account, which is why this is its own table rather than columns on
    User."""


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    oauth_accounts: Mapped[list[OAuthAccount]] = relationship("OAuthAccount", lazy="joined")
