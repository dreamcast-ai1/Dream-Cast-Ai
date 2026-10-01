"""email verification (OTP), password reset tokens, Google sign-in columns

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None
TS = lambda: sa.DateTime(timezone=True)  # noqa: E731


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("google_subject_id", sa.String(64), nullable=True))
    op.create_index("ix_users_google_subject_id", "users", ["google_subject_id"], unique=True)
    # Accounts that already exist keep working: they signed up before verification existed, so they count as verified.
    op.get_bind().execute(sa.text("UPDATE users SET email_verified = :t"), {"t": True})

    op.create_table(
        "email_verifications",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS(), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("used_at", TS(), nullable=True),
        sa.Column("created_at", TS(), nullable=False),
    )
    op.create_index("ix_email_verifications_user_id", "email_verifications", ["user_id"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS(), nullable=False),
        sa.Column("used_at", TS(), nullable=True),
        sa.Column("created_at", TS(), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_table("email_verifications")
    op.drop_index("ix_users_google_subject_id", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("google_subject_id")
        batch.drop_column("email_verified")
