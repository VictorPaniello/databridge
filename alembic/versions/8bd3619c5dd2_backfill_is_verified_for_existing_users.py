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

    Written for a required-email-verification feature that was built,
    tested end-to-end in production, and then deliberately reverted
    before release (see the README's "What it doesn't do (yet)" and the
    CHANGELOG) - real Resend delivery only reaches the Resend account's
    own address without a verified custom domain, and enforcing
    verification with delivery that broken would have permanently locked
    out every real registrant but the account owner. is_verified itself
    (an existing fastapi-users column, SQLAlchemyBaseUserTableUUID, not
    new here) is unused application-side again now - nothing reads it -
    but this migration is left in place rather than reverted: it already
    ran in production, it's harmless (a bulk backfill to `true`, which is
    also just correct - every account that predates a verification
    policy that no longer exists should read as verified), and there's
    no reason to touch already-applied migration history for a change
    that isn't semantically wrong, just no longer acted on.
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
