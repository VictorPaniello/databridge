"""add has_password to users

Revision ID: 92b042e40fb0
Revises: ab7e5507eaf3
Create Date: 2026-09-10 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '92b042e40fb0'
down_revision: Union[str, Sequence[str], None] = 'ab7e5507eaf3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    hashed_password is never NULL - a GitHub-OAuth-only signup gets a
    random, nobody-knows-it value there too (fastapi-users'
    oauth_callback), so the column alone can't tell a real, user-chosen
    password apart from that placeholder. has_password is the explicit
    flag auth.py's UserManager now maintains (set True on email+password
    registration and on any password change/reset) - /auth/forgot-password
    checks it before ever issuing a reset token, so a GitHub-only account
    can't have a password bootstrapped onto it through an unauthenticated
    email link.

    Backfill: server_default 'false' covers every row as the column is
    added, then the UPDATE below flips it to true for every user with no
    linked OAuth account - by definition, the only way to reach that state
    is having registered with a real email+password (auth.py's
    UserManager.create(), which oauth_callback's own user creation path
    never goes through). A user who signed up via GitHub *and has since
    set a real password through Settings* is indistinguishable from a
    GitHub-only user by anything this migration can inspect - they'll
    read as has_password=false until they change their password once
    through Settings again, which flips it right back (on_after_update).
    """
    op.add_column(
        'users', sa.Column('has_password', sa.Boolean(), nullable=False, server_default='false')
    )
    op.execute(
        """
        UPDATE users
        SET has_password = true
        WHERE id NOT IN (SELECT DISTINCT user_id FROM oauth_account)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'has_password')
