from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..generators import BY_ID, GENERATORS
from ..models import UsageRecord
from ..plans import DEFAULT_PLAN_ID
from . import subscriptions


def get_limits(db: Session, plan_id: str = DEFAULT_PLAN_ID) -> dict[str, int]:
    """Limits of one plan (admin overrides included). Kept for the admin screens; user-facing checks use limits_for_user."""
    from ..plans import get_plan
    return subscriptions.plan_limits(db, get_plan(plan_id))


def set_limits(db: Session, new: dict[str, int], plan_id: str = DEFAULT_PLAN_ID) -> dict[str, int]:
    return subscriptions.set_plan_limits(db, plan_id, new)


def window_start(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.replace(day=1) if period == "month" else start


def window_end(period: str) -> datetime:
    start = window_start(period)
    if period == "month":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start + timedelta(days=1)


def used_in_period(db: Session, user_id: str, period: str) -> dict[str, int]:
    rows = db.execute(
        select(UsageRecord.generator_type, func.coalesce(func.sum(UsageRecord.request_count), 0))
        .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= window_start(period))
        .group_by(UsageRecord.generator_type)
    ).all()
    return {g: int(n) for g, n in rows}


def used_today(db: Session, user_id: str) -> dict[str, int]:
    """Usage in the user's plan period (name kept: the default period is a day)."""
    return used_in_period(db, user_id, subscriptions.plan_for_user_id(db, user_id).usage_period)


def summary(db: Session, user_id: str) -> list[dict]:
    plan = subscriptions.plan_for_user_id(db, user_id)
    limits, used = subscriptions.plan_limits(db, plan), used_in_period(db, user_id, plan.usage_period)
    return [{"generator": g.id, "label": g.label, "emoji": g.emoji, "used": used.get(g.id, 0), "limit": limits[g.id],
             "remaining": max(0, limits[g.id] - used.get(g.id, 0))} for g in GENERATORS]


def remaining(db: Session, user_id: str, generator: str) -> int:
    plan = subscriptions.plan_for_user_id(db, user_id)
    return max(0, subscriptions.plan_limits(db, plan)[generator] - used_in_period(db, user_id, plan.usage_period).get(generator, 0))


def has_quota(db: Session, user_id: str, generator: str) -> bool:
    """The only quota check; it runs on the server when a job is created, so the UI can't be bypassed by calling the API directly."""
    return remaining(db, user_id, generator) > 0


def record(db: Session, user_id: str, generator: str, *, provider: str | None = None, status: str = "SUCCEEDED",
           units: float = 0.0, cost_estimate: float = 0.0, job_id: str | None = None) -> UsageRecord:
    if generator not in BY_ID and generator != "refinement":
        raise ValueError(f"Unknown generator '{generator}'")
    rec = UsageRecord(user_id=user_id, generator_type=generator, provider=provider, status=status,
                      units=units, cost_estimate=cost_estimate, job_id=job_id)
    db.add(rec)
    db.commit()
    return rec


# --- per-job accounting: reserve on submit, keep or give back depending on how the job ends -------------

def reserve(db: Session, user_id: str, generator: str, job_id: str, provider: str | None = None) -> UsageRecord:
    rec = UsageRecord(user_id=user_id, generator_type=generator, status="RESERVED", job_id=job_id, request_count=1,
                      provider=provider)
    db.add(rec)
    return rec


def settle(db: Session, job_id: str, *, refund: bool, provider: str | None = None, status: str = "SUCCEEDED",
           units: float = 0.0, cost_estimate: float = 0.0) -> None:
    """refund=True gives the allowance back (the request never produced work at a provider)."""
    rec = db.scalars(select(UsageRecord).where(UsageRecord.job_id == job_id)).first()
    if not rec:
        return
    rec.provider = provider or rec.provider
    if refund:
        rec.status, rec.request_count = "REFUNDED", 0
    else:
        rec.status, rec.units, rec.cost_estimate = status, units, cost_estimate
    db.commit()


def provider_used_today(db: Session, provider: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(UsageRecord.request_count), 0))
                         .where(UsageRecord.provider == provider, UsageRecord.created_at >= window_start("day"))) or 0)


def count_refinement(db: Session, user_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(UsageRecord.request_count), 0))
                         .where(UsageRecord.user_id == user_id, UsageRecord.generator_type == "refinement",
                                UsageRecord.created_at >= window_start("day"))) or 0)
