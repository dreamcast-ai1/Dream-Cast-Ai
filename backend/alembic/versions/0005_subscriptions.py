"""subscriptions: one row per user (plan, status, renewal, payment-provider references)

Revision ID: 0005
Revises: 0004
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    subs = op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.String(40), nullable=False, server_default="audience"),
        sa.Column("status", sa.String(15), nullable=False, server_default="ACTIVE"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_provider", sa.String(30), nullable=True),
        sa.Column("provider_customer_id", sa.String(100), nullable=True),
        sa.Column("provider_subscription_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=True)
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"])

    # Existing users all start on the free "audience" plan. Plain Python inserts keep this portable (SQLite and PostgreSQL).
    now = datetime.now(timezone.utc)
    users = op.get_bind().execute(sa.text("SELECT id FROM users")).fetchall()
    if users:
        op.bulk_insert(subs, [{"id": uuid.uuid4().hex, "user_id": row[0], "plan_id": "audience", "status": "ACTIVE", "started_at": now,
                               "expires_at": None, "payment_provider": None, "provider_customer_id": None,
                               "provider_subscription_id": None, "created_at": now, "updated_at": now} for row in users])


def downgrade() -> None:
    op.drop_index("ix_subscriptions_plan_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
