"""support tickets and messages (in-app Support assistant)

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None
TS = lambda: sa.DateTime(timezone=True)  # noqa: E731


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("page", sa.String(80), nullable=True),
        sa.Column("feature", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("diagnostic_context", sa.JSON, nullable=False),
        sa.Column("admin_response", sa.Text, nullable=True),
        sa.Column("created_at", TS(), nullable=False),
        sa.Column("updated_at", TS(), nullable=False),
        sa.Column("resolved_at", TS(), nullable=True),
    )
    op.create_index("ix_support_tickets_number", "support_tickets", ["number"], unique=True)
    for col in ("user_id", "category", "status", "priority"):
        op.create_index(f"ix_support_tickets_{col}", "support_tickets", [col])
    op.create_table(
        "support_messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("ticket_id", sa.String(32), sa.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author", sa.String(10), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", TS(), nullable=False),
    )
    op.create_index("ix_support_messages_ticket_id", "support_messages", ["ticket_id"])


def downgrade() -> None:
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
