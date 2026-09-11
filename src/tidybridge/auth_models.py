"""User identity tables.

Kept separate from models.py (which holds tidybridge's own domain
tables) because these are fastapi-users' tables - id, email, hashed
password, active/verified flags, and linked OAuth accounts (e.g. GitHub)
are all managed by that library, not hand-rolled here."""

from __future__ import annotations

import uuid

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tidybridge.db import Base


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

    # Nullable at the DB level even though registration requires
    # first_name/last_name going forward (see auth.py's UserCreate) -
    # existing users (registered before this field existed, including via
    # GitHub OAuth, which bypasses UserCreate entirely and never populates
    # these) have NULL here. The frontend falls back to showing the email
    # when first_name is unset rather than assuming every user has one.
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # hashed_password (inherited from the mixin) is never NULL - a GitHub-
    # OAuth-only signup gets a random, nobody-knows-it hash there too (see
    # fastapi_users' BaseUserManager.oauth_callback), so that column alone
    # can't tell a real, user-chosen password apart from that placeholder.
    # This flag is what auth.py's UserManager maintains explicitly (set on
    # email+password registration and on any password change/reset) so
    # /auth/forgot-password can refuse to issue a reset token - and
    # therefore a real password - to an account nobody ever put one on.
    has_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    oauth_accounts: Mapped[list[OAuthAccount]] = relationship("OAuthAccount", lazy="joined")
