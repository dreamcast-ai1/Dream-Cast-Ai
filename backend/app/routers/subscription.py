from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import User
from ..plans import Plan, public_plans
from ..security import RateLimiter
from ..services import payments, subscriptions, usage

router = APIRouter(prefix="/api/subscription", tags=["subscription"])

checkout_rate_limit = RateLimiter(lambda: get_settings().checkout_rate_limit_per_minute)     # per IP per minute: stops order spam against the payment gateway


def plan_out(db: Session, p: Plan) -> dict:
    return {"id": p.id, "name": p.name, "tagline": p.tagline, "description": p.description, "price_minor": p.price_minor,
            "currency": p.currency, "billing_period": p.billing_period, "usage_period": p.usage_period,
            "limits": subscriptions.plan_limits(db, p), "features": dict(p.features)}


def _usage(db: Session, user: User) -> dict:
    plan, _ = subscriptions.plan_for_user(db, user)
    return {"plan_id": plan.id, "period": plan.usage_period, "resets_at": usage.window_end(plan.usage_period).isoformat(),
            "items": usage.summary(db, user.id)}


@router.get("/plans")
def plans(db: Session = Depends(get_db)):
    """Public plan catalogue (prices come from server settings)."""
    return {"plans": [plan_out(db, p) for p in public_plans()], "payments_enabled": payments.payments_enabled()}


@router.get("/current")
def current(user: User = Depends(current_user), db: Session = Depends(get_db)):
    plan, sub = subscriptions.plan_for_user(db, user)
    return {"plan": plan_out(db, plan), "payments_enabled": payments.payments_enabled(),
            "subscription": {"plan_id": sub.plan_id, "effective_plan_id": plan.id, "status": sub.status, "started_at": sub.started_at,
                             "expires_at": sub.expires_at, "payment_provider": sub.payment_provider}}


@router.get("/usage")
def my_usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _usage(db, user)


@router.get("/entitlements")
def my_entitlements(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return subscriptions.entitlements(db, user)


class CheckoutIn(BaseModel):
    plan_id: str = Field(max_length=40)


class VerifyIn(BaseModel):
    razorpay_order_id: str = Field(max_length=100)
    razorpay_payment_id: str = Field(max_length=100)
    razorpay_signature: str = Field(max_length=200)


class CancelIn(BaseModel):
    order_id: str = Field(max_length=100)
    reason: str = Field(default="cancelled", pattern="^(cancelled|failed)$")


@router.post("/checkout", dependencies=[Depends(checkout_rate_limit)])
def checkout(body: CheckoutIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Creates a payment order for a paid plan. This does NOT change the plan: only a verified payment does."""
    return payments.create_checkout(db, user, body.plan_id)


@router.post("/verify")
def verify(body: VerifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Called by the browser after Checkout. The signature is checked on the server; repeating the call is harmless."""
    return payments.confirm_payment(db, user, body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature)


@router.post("/checkout/cancel")
def cancel(body: CancelIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return payments.cancel_checkout(db, user, body.order_id, body.reason)


@router.get("/payments")
def my_payments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": payments.user_payments(db, user)}
