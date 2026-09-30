from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..generators import BY_ID, GENERATORS
from ..models import AppSetting, UsageRecord

LIMITS_KEY = "daily_limits"


def default_limits() -> dict[str, int]:
    return {g.id: g.default_daily_limit for g in GENERATORS}


def get_limits(db: Session) -> dict[str, int]:
    row = db.get(AppSetting, LIMITS_KEY)
    limits = default_limits()
    if row:
        limits.update({k: int(v) for k, v in row.value.items() if k in BY_ID})
    return limits


def set_limits(db: Session, new: dict[str, int]) -> dict[str, int]:
    limits = get_limits(db)
    limits.update({k: max(0, int(v)) for k, v in new.items() if k in BY_ID})
    row = db.get(AppSetting, LIMITS_KEY)
    if row:
        row.value = limits
    else:
        db.add(AppSetting(key=LIMITS_KEY, value=limits))
    db.commit()
    return limits


def _today_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def used_today(db: Session, user_id: str) -> dict[str, int]:
    rows = db.execute(
        select(UsageRecord.generator_type, func.coalesce(func.sum(UsageRecord.request_count), 0))
        .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= _today_start())
        .group_by(UsageRecord.generator_type)
    ).all()
    return {g: int(n) for g, n in rows}


def summary(db: Session, user_id: str) -> list[dict]:
    limits, used = get_limits(db), used_today(db, user_id)
    return [
        {"generator": g.id, "label": g.label, "emoji": g.emoji, "used": used.get(g.id, 0), "limit": limits[g.id]}
        for g in GENERATORS
    ]


def has_quota(db: Session, user_id: str, generator: str) -> bool:
    return used_today(db, user_id).get(generator, 0) < get_limits(db)[generator]


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
                         .where(UsageRecord.provider == provider, UsageRecord.created_at >= _today_start())) or 0)


def count_refinement(db: Session, user_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(UsageRecord.request_count), 0))
                         .where(UsageRecord.user_id == user_id, UsageRecord.generator_type == "refinement",
                                UsageRecord.created_at >= _today_start())) or 0)
