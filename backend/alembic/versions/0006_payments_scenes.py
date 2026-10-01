"""payments, payment webhook events, movie scenes; rename plans to Trailer / Indie / Blockbuster

Revision ID: 0006
Revises: 0005
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Old (Phase 5 draft) plan ids -> current ids. "studio" no longer exists; its users move to the nearest plan (blockbuster).
RENAMES = {"audience": "trailer", "indie_director": "indie", "studio": "blockbuster"}
TS = lambda: sa.DateTime(timezone=True)  # noqa: E731


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_order_id", sa.String(100), nullable=False),
        sa.Column("provider_payment_id", sa.String(100), nullable=True),
        sa.Column("amount_minor", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("failure_reason", sa.String(200), nullable=True),
        sa.Column("paid_at", TS(), nullable=True),
        sa.Column("created_at", TS(), nullable=False),
        sa.Column("updated_at", TS(), nullable=False),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_provider_order_id", "payments", ["provider_order_id"], unique=True)
    op.create_index("ix_payments_provider_payment_id", "payments", ["provider_payment_id"], unique=True)
    op.create_index("ix_payments_status", "payments", ["status"])

    op.create_table(
        "payment_events",
        sa.Column("event_id", sa.String(100), primary_key=True),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("created_at", TS(), nullable=False),
    )

    op.create_table(
        "scenes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("script", sa.Text, nullable=False),
        sa.Column("character_ids", sa.JSON, nullable=False),
        sa.Column("visual_prompt", sa.Text, nullable=False),
        sa.Column("duration_seconds", sa.Integer, nullable=False),
        sa.Column("video_asset_id", sa.String(32), nullable=True),
        sa.Column("last_job_id", sa.String(32), nullable=True),
        sa.Column("script_asset_id", sa.String(32), nullable=True),
        sa.Column("created_at", TS(), nullable=False),
        sa.Column("updated_at", TS(), nullable=False),
    )
    op.create_index("ix_scenes_project_id", "scenes", ["project_id"])
    op.create_index("ix_scenes_user_id", "scenes", ["user_id"])
    op.create_index("ix_scenes_project_number", "scenes", ["project_id", "number"])

    # Existing users keep working: map old plan ids to the new ones (everyone on the free plan stays free).
    bind = op.get_bind()
    for old, new in RENAMES.items():
        bind.execute(sa.text("UPDATE subscriptions SET plan_id = :new WHERE plan_id = :old"), {"new": new, "old": old})
    row = bind.execute(sa.text("SELECT value FROM app_settings WHERE key = 'plan_limits'")).fetchone()
    if row:                                     # admin limit overrides were stored per old plan id
        value = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        moved = {}
        for k, v in value.items():
            if k == "studio" and "blockbuster" in value:      # an explicit blockbuster override wins over the retired studio one
                continue
            moved[RENAMES.get(k, k)] = v
        bind.execute(sa.text("UPDATE app_settings SET value = :v WHERE key = 'plan_limits'"), {"v": json.dumps(moved)})


def downgrade() -> None:
    bind = op.get_bind()
    for old, new in RENAMES.items():
        if old != "studio":
            bind.execute(sa.text("UPDATE subscriptions SET plan_id = :old WHERE plan_id = :new"), {"new": new, "old": old})
    op.drop_table("scenes")
    op.drop_table("payment_events")
    op.drop_table("payments")
