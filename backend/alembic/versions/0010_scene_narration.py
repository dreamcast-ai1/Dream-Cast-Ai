"""scene narration: the voice asset (and its job) a scene uses in the assembled movie

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scenes") as b:
        b.add_column(sa.Column("narration_asset_id", sa.String(32), nullable=True))
        b.add_column(sa.Column("narration_job_id", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scenes") as b:
        b.drop_column("narration_job_id")
        b.drop_column("narration_asset_id")
