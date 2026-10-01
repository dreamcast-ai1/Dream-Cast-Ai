"""Resolves a user's effective plan, limits and entitlements. The single place routers/services ask 'what may this user do?'."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..errors import AppError
from ..generators import BY_ID
from ..models import AppSetting, Subscription, User
from ..plans import DEFAULT_PLAN_ID, PLANS, Plan, get_plan

PLAN_LIMITS_KEY = "plan_limits"      # admin overrides: {plan_id: {generator_id: limit}}
LEGACY_LIMITS_KEY = "daily_limits"   # pre-subscription admin overrides; they applied to everyone, so they now apply to the free plan
USABLE_STATUSES = ("ACTIVE", "PAST_DUE")   # PAST_DUE keeps access during a payment grace period; CANCELLED/EXPIRED fall back to free


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_subscription(db: Session, user: User) -> Subscription:
    """Every user always has a subscription row (new users and anyone created before subscriptions existed)."""
    sub = user.subscription or db.query(Subscription).filter_by(user_id=user.id).first()
    if sub is None:
        sub = Subscription(user_id=user.id, plan_id=DEFAULT_PLAN_ID, status="ACTIVE", started_at=_now())
        db.add(sub)
        db.commit()
    return sub


def effective_plan(sub: Subscription) -> Plan:
    expired = sub.expires_at is not None and sub.expires_at <= _now()
    if sub.status not in USABLE_STATUSES or expired:
        return PLANS[DEFAULT_PLAN_ID]
    return get_plan(sub.plan_id)


def plan_for_user(db: Session, user: User) -> tuple[Plan, Subscription]:
    sub = ensure_subscription(db, user)
    return effective_plan(sub), sub


def plan_for_user_id(db: Session, user_id: str) -> Plan:
    user = db.get(User, user_id)
    return plan_for_user(db, user)[0] if user else PLANS[DEFAULT_PLAN_ID]


def _override_row(db: Session, key: str) -> dict:
    row = db.get(AppSetting, key)
    return dict(row.value) if row else {}


def plan_limits(db: Session, plan: Plan) -> dict[str, int]:
    """Plan limits with admin overrides applied (new per-plan overrides win over the legacy free-plan ones)."""
    limits = dict(plan.limits)
    if plan.id == DEFAULT_PLAN_ID:
        limits.update({k: int(v) for k, v in _override_row(db, LEGACY_LIMITS_KEY).items() if k in BY_ID})
    limits.update({k: int(v) for k, v in _override_row(db, PLAN_LIMITS_KEY).get(plan.id, {}).items() if k in BY_ID})
    return limits


def set_plan_limits(db: Session, plan_id: str, new: dict[str, int]) -> dict[str, int]:
    if plan_id not in PLANS:
        raise AppError("Unknown plan.", 404, "not_found")
    data = _override_row(db, PLAN_LIMITS_KEY)
    mine = {**plan_limits(db, PLANS[plan_id]), **{k: max(0, int(v)) for k, v in new.items() if k in BY_ID}}
    data[plan_id] = mine
    row = db.get(AppSetting, PLAN_LIMITS_KEY)
    if row:
        row.value = data
    else:
        db.add(AppSetting(key=PLAN_LIMITS_KEY, value=data))
    db.commit()
    return mine


def limits_for_user(db: Session, user_id: str) -> dict[str, int]:
    return plan_limits(db, plan_for_user_id(db, user_id))


def entitlements(db: Session, user: User) -> dict:
    plan, _ = plan_for_user(db, user)
    return {"plan_id": plan.id, "features": dict(plan.features), "limits": plan_limits(db, plan), "usage_period": plan.usage_period}


def require_feature(db: Session, user: User, feature: str, message: str) -> None:
    plan, _ = plan_for_user(db, user)
    if not plan.features.get(feature):
        raise AppError(message, 403, "plan_feature")


def set_user_plan(db: Session, user: User, plan_id: str, *, days: int | None = None, provider: str = "admin") -> Subscription:
    """Moves a user to a plan. Used by admins today; a payment webhook would call the same function later.
    This never charges anyone and stores no payment details."""
    if plan_id not in PLANS or not PLANS[plan_id].active:
        raise AppError("Unknown plan.", 404, "not_found")
    sub = ensure_subscription(db, user)
    sub.plan_id, sub.status = plan_id, "ACTIVE"
    sub.started_at = _now()
    sub.expires_at = _now() + timedelta(days=days) if days and plan_id != DEFAULT_PLAN_ID else None
    sub.payment_provider = None if plan_id == DEFAULT_PLAN_ID else provider
    db.commit()
    return sub
