"""generation engine: standard request fields on generation_jobs

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generation_jobs") as b:
        b.add_column(sa.Column("original_prompt", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("refined_prompt", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("options", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("reference_assets", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("context", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("stage", sa.String(15), nullable=False, server_default="QUEUED"))
        b.add_column(sa.Column("error_code", sa.String(30), nullable=True))
        b.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("parent_id", sa.String(32), nullable=True))
        b.add_column(sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("external_id", sa.String(100), nullable=True))
        b.add_column(sa.Column("reached_provider", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()))
        b.alter_column("progress", existing_type=sa.Integer(), nullable=True)
        b.create_index("ix_generation_jobs_parent_id", ["parent_id"])
    # Phase 1 stored a meaningless 0 for progress; only real provider progress is kept now.
    op.execute("UPDATE generation_jobs SET progress = NULL")
    op.execute("UPDATE generation_jobs SET stage = status WHERE status IN ('COMPLETED','FAILED','CANCELLED','PROCESSING')")


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs") as b:
        b.drop_index("ix_generation_jobs_parent_id")
        for c in ("updated_at", "retry_at", "reached_provider", "external_id", "cancel_requested", "parent_id", "attempts",
                  "error_code", "stage", "context", "reference_assets", "options", "refined_prompt", "original_prompt"):
            b.drop_column(c)
        b.alter_column("progress", existing_type=sa.Integer(), nullable=False, server_default="0")
