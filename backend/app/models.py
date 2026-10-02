import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo; this keeps every datetime timezone-aware UTC in and out of the DB."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)    # PostgreSQL answers in the session's zone
        return value


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampedMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class User(Base, TimestampedMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(30), default="local")  # local | google | email ...
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(10), default="USER")  # USER | ADMIN
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)           # proven by a one-time code or by Google
    google_subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)   # Google's stable user id ("sub")

    projects: Mapped[list["Project"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="user", cascade="all, delete-orphan", uselist=False)


class EmailVerification(Base, TimestampedMixin):
    """One sign-up code. Only a keyed hash of the code is stored. A code is single-use: used_at is set when it is spent,
    replaced (resend) or locked after too many wrong guesses."""
    __tablename__ = "email_verifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PasswordResetToken(Base, TimestampedMixin):
    """A password reset link token. Only the SHA-256 of the token is stored; the token itself exists only in the email."""
    __tablename__ = "password_reset_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Project(Base, TimestampedMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, index=True)

    user: Mapped[User] = relationship(back_populates="projects")
    characters: Mapped[list["Character"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    references: Mapped[list["ReferenceAsset"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    assets: Mapped[list["GeneratedAsset"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    jobs: Mapped[list["GenerationJob"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Character(Base, TimestampedMixin):
    __tablename__ = "characters"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    age: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    clothing: Mapped[str] = mapped_column(Text, default="")
    reference_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    project: Mapped[Project] = relationship(back_populates="characters")


class ReferenceAsset(Base, TimestampedMixin):
    __tablename__ = "reference_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20), default="OTHER")
    name: Mapped[str] = mapped_column(String(200), default="")
    file_path: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="references")


class GenerationJob(Base, TimestampedMixin):
    __tablename__ = "generation_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(30))  # generator id
    status: Mapped[str] = mapped_column(String(15), default="QUEUED", index=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)  # only set when a provider reports real progress
    input_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    output_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- standard generation request (Phase 2)
    original_prompt: Mapped[str] = mapped_column(Text, default="")
    refined_prompt: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    reference_assets: Mapped[list] = mapped_column(JSON, default=list)   # ReferenceAsset ids
    context: Mapped[dict] = mapped_column(JSON, default=dict)            # context snapshot given to the provider
    stage: Mapped[str] = mapped_column(String(15), default="QUEUED")    # QUEUED PREPARING GENERATING PROCESSING RETRYING ...
    error_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # job this one regenerates
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    external_id: Mapped[str | None] = mapped_column(String(600), nullable=True)   # provider job reference (JSON for queue APIs)
    reached_provider: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="jobs")


class GeneratedAsset(Base, TimestampedMixin):
    __tablename__ = "generated_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True)
    type: Mapped[str] = mapped_column(String(15), index=True)  # VIDEO MUSIC VOICE LYRICS STORY SCRIPT FACE AVATAR
    title: Mapped[str] = mapped_column(String(200), default="")
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # file-based assets
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)      # text assets (story/script/lyrics)
    prompt: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="READY")
    # --- versions and content details (Phase 3). Regenerating never overwrites: it adds a new version to the same lineage.
    version: Mapped[int] = mapped_column(Integer, default=1)
    lineage_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    format: Mapped[str] = mapped_column(String(10), default="")          # txt for text assets; mp3/wav/flac for audio
    language: Mapped[str] = mapped_column(String(20), default="")
    mime_type: Mapped[str] = mapped_column(String(60), default="")
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    project: Mapped[Project] = relationship(back_populates="assets")


class UsageRecord(Base, TimestampedMixin):
    __tablename__ = "usage_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True)
    generator_type: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="SUCCEEDED")  # SUCCEEDED | FAILED
    request_count: Mapped[int] = mapped_column(Integer, default=1)
    units: Mapped[float] = mapped_column(Float, default=0.0)  # provider-specific (seconds, chars, credits...)
    cost_estimate: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("ix_usage_user_created", "user_id", "created_at"),)


class Notification(Base, TimestampedMixin):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(30), default="info")  # info | job_completed | job_failed
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)   # the result, for completed generations


class Subscription(Base, TimestampedMixin):
    """One row per user. plan_id refers to a key in app.plans.PLANS (plans are configuration, not data).
    No payment credentials are ever stored here; the payment provider holds those and we keep only opaque references."""
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(40), default="teaser", index=True)
    status: Mapped[str] = mapped_column(String(15), default="ACTIVE")    # ACTIVE | PAST_DUE | CANCELLED | EXPIRED
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)   # renewal/expiry; None = never (free plan)
    payment_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)     # "razorpay" or "admin"
    provider_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    user: Mapped["User"] = relationship(back_populates="subscription")


class Payment(Base, TimestampedMixin):
    """One checkout attempt (a payment-provider order). Holds only opaque provider references and amounts, never card data.
    status: CREATED -> PAID, or CREATED -> FAILED/CANCELLED (a verified payment can still move those to PAID)."""
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(30), default="razorpay")
    provider_order_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)          # paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(15), default="CREATED", index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)


class PaymentEvent(Base):
    """Webhook event ids already handled: a repeated delivery of the same event is ignored."""
    __tablename__ = "payment_events"
    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


class Scene(Base, TimestampedMixin):
    """One scene of a project's movie. Generating its clip uses the normal video job (and video allowance);
    creating or editing a scene costs nothing."""
    __tablename__ = "scenes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    script: Mapped[str] = mapped_column(Text, default="")
    character_ids: Mapped[list] = mapped_column(JSON, default=list)
    visual_prompt: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=10)     # one clip: 10..30 s
    video_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)   # the clip used in the movie
    last_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    narration_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)   # the spoken narration used in the movie
    narration_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    script_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # script this scene came from, if any
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    __table_args__ = (Index("ix_scenes_project_number", "project_id", "number"),)


class AppSetting(Base):
    """Key/value store for admin-editable configuration (e.g. daily generation limits)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)


class SupportTicket(Base, TimestampedMixin):
    """A problem a user sent to the admin team from the in-app Support assistant. `number` is the public reference (DC-1042); the
    database id is never shown. diagnostic_context holds only the small allow-listed, scrubbed fields the support service accepts."""
    __tablename__ = "support_tickets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(20), default="GENERAL", index=True)
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)      # open | in_progress | resolved | closed
    priority: Mapped[str] = mapped_column(String(10), default="normal", index=True)  # low | normal | high | critical (set by the server)
    page: Mapped[str | None] = mapped_column(String(80), nullable=True)
    feature: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    diagnostic_context: Mapped[dict] = mapped_column(JSON, default=dict)
    admin_response: Mapped[str | None] = mapped_column(Text, nullable=True)           # the latest admin reply, for quick display
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    messages: Mapped[list["SupportMessage"]] = relationship(back_populates="ticket", cascade="all, delete-orphan", order_by="SupportMessage.created_at")


class SupportMessage(Base, TimestampedMixin):
    """Follow-ups on a ticket: more information from the user, or a reply from an admin."""
    __tablename__ = "support_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(10))       # user | admin
    body: Mapped[str] = mapped_column(Text)

    ticket: Mapped[SupportTicket] = relationship(back_populates="messages")
