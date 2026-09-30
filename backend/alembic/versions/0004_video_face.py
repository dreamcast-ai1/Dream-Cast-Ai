"""notifications link to the result asset; provider job references can be longer than 100 chars

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as b:
        b.add_column(sa.Column("asset_id", sa.String(32), nullable=True))
    with op.batch_alter_table("generation_jobs") as b:
        b.alter_column("external_id", existing_type=sa.String(100), type_=sa.String(600), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs") as b:
        b.alter_column("external_id", existing_type=sa.String(600), type_=sa.String(100), existing_nullable=True)
    with op.batch_alter_table("notifications") as b:
        b.drop_column("asset_id")
