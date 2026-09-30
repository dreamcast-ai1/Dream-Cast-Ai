"""generated assets: versions, lineage and content details

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generated_assets") as b:
        b.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        b.add_column(sa.Column("lineage_id", sa.String(32), nullable=True))
        b.add_column(sa.Column("format", sa.String(10), nullable=False, server_default=""))
        b.add_column(sa.Column("language", sa.String(20), nullable=False, server_default=""))
        b.add_column(sa.Column("mime_type", sa.String(60), nullable=False, server_default=""))
        b.add_column(sa.Column("duration_seconds", sa.Float(), nullable=True))
        b.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()))
        b.create_index("ix_generated_assets_lineage_id", ["lineage_id"])
    # Existing (Phase 2) assets become version 1 of their own lineage.
    op.execute("UPDATE generated_assets SET lineage_id = id, updated_at = created_at, format = CASE WHEN text_content IS NOT NULL THEN 'txt' ELSE '' END")


def downgrade() -> None:
    with op.batch_alter_table("generated_assets") as b:
        b.drop_index("ix_generated_assets_lineage_id")
        for c in ("updated_at", "duration_seconds", "mime_type", "language", "format", "lineage_id", "version"):
            b.drop_column(c)
