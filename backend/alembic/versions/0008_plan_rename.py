"""rename plans: trailer->teaser, indie->trailer, blockbuster->movie (monthly allowances)

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# Order matters: "trailer" changes meaning, so the old free plan is moved out of the way first and the old paid plan is moved last.
FORWARD = [("audience", "teaser"), ("trailer", "teaser"), ("indie_director", "trailer"), ("indie", "trailer"),
           ("studio", "movie"), ("blockbuster", "movie")]
BACKWARD = [("trailer", "indie"), ("teaser", "trailer"), ("movie", "blockbuster")]


def _remap(pairs) -> None:
    bind = op.get_bind()
    for table in ("subscriptions", "payments"):
        for old, new in pairs:
            bind.execute(sa.text(f"UPDATE {table} SET plan_id = :new WHERE plan_id = :old"), {"old": old, "new": new})
    # Admin allowance overrides were per day and keyed by the old ids; they no longer describe a plan, so they are dropped.
    bind.execute(sa.text("DELETE FROM app_settings WHERE key IN ('plan_limits', 'daily_limits')"))


def upgrade() -> None:
    _remap(FORWARD)


def downgrade() -> None:
    _remap(BACKWARD)
