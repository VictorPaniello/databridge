"""backfill is_verified for existing users

Revision ID: 8bd3619c5dd2
Revises: 92b042e40fb0
Create Date: 2026-09-10 19:20:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8bd3619c5dd2'
down_revision: Union[str, Sequence[str], None] = '92b042e40fb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    is_verified is an existing fastapi-users column (SQLAlchemyBaseUserTableUUID),
    not new here - it's just never been enforced or sent anywhere until this
    change wires up the verify-email flow and starts gating /records/* on it
    (see main.py's current_verified_active_user). Every account that already
    exists predates that policy entirely, GitHub OAuth ones included (that
    router only starts passing is_verified_by_default=True with this same
    change) - none of them ever got a verification email to click, so
    leaving them at whatever is_verified already holds would lock every
    current user, including the real accounts this project has been tested
    with all session, out of their own records on the next deploy. This is
    a one-time grandfather clause: only new registrations from here on
    actually go through the real flow.
    """
    op.execute("UPDATE users SET is_verified = true WHERE is_verified = false")


def downgrade() -> None:
    """Downgrade schema.

    Deliberately a no-op: there's no way to know which of these was
    genuinely verified through the new flow after this migration ran vs.
    grandfathered in by it, so reversing it can't be done correctly -
    downgrading past this point should be treated as "not supported".
    """
    pass
