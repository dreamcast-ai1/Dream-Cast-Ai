"""Checkout, payment verification and webhook handling. Gateway-specific work lives behind app.payments.PaymentProvider.
The browser's claim that a payment succeeded is never trusted: a plan is activated only after a verified signature
(from the checkout callback or from a verified webhook), and activation is idempotent."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AppError, NotFound
from ..models import Payment, PaymentEvent as PaymentEventRow, User
from ..payments import PaymentProviderError, get_payment_provider
from ..plans import DEFAULT_PLAN_ID, PLANS
from . import subscriptions

log = logging.getLogger("dreamcast.payments")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def payments_enabled() -> bool:
    return get_payment_provider().is_configured()


def create_checkout(db: Session, user: User, plan_id: str) -> dict:
    plan = PLANS.get(plan_id)
    if not plan or not plan.active or not plan.is_paid:
        raise AppError("Choose a paid plan to upgrade to.", 422, "invalid_plan")
    current, _ = subscriptions.plan_for_user(db, user)
    if current.id == plan.id and _has_long_runway(db, user):
        raise AppError(f"You're already on the {plan.name} plan.", 409, "already_subscribed")
    provider = get_payment_provider()
    if not provider.is_configured():
        raise AppError("Payments aren't available yet. Please try again later.", 503, "payments_not_configured")
    try:
        order = provider.create_order(plan.price_minor, plan.currency, f"dc_{user.id[:12]}_{plan.id}",
                                      {"user_id": user.id, "plan_id": plan.id})
    except PaymentProviderError as e:
        log.warning("create_order failed: %s", e.detail or e.message)
        raise AppError(e.message, 502, "payment_provider_error")
    # The amount charged is the server's price, whatever the browser says.
    db.add(Payment(user_id=user.id, plan_id=plan.id, provider=provider.name, provider_order_id=order.id,
                   amount_minor=order.amount_minor, currency=order.currency))
    db.commit()
    return {"order_id": order.id, "amount": order.amount_minor, "currency": order.currency, "key_id": provider.public_key(),
            "plan": {"id": plan.id, "name": plan.name}, "provider": provider.name, "user_email": user.email, "user_name": user.name}


def _has_long_runway(db: Session, user: User) -> bool:
    """Renewing early is allowed; re-buying the same plan right after paying is not (avoids accidental double charges)."""
    sub = subscriptions.ensure_subscription(db, user)
    return sub.expires_at is not None and sub.expires_at > _now() + timedelta(days=get_settings().plan_billing_days - 1)


def _own_payment(db: Session, user: User, order_id: str) -> Payment:
    p = db.scalars(select(Payment).where(Payment.provider_order_id == order_id, Payment.user_id == user.id)).first()
    if not p:
        raise NotFound("Payment not found.")        # same answer for "doesn't exist" and "belongs to someone else"
    return p


def _activate(db: Session, payment: Payment, provider_payment_id: str | None) -> bool:
    """Marks the payment PAID and activates the plan, once. Returns False if it was already paid (duplicate callback/webhook)."""
    try:
        res = db.execute(update(Payment).where(Payment.id == payment.id, Payment.status != "PAID")
                         .values(status="PAID", provider_payment_id=provider_payment_id, paid_at=_now(), failure_reason=None))
    except IntegrityError:                                   # the same gateway payment id already belongs to another order
        db.rollback()
        raise AppError("This payment has already been used.", 409, "payment_reused")
    if res.rowcount != 1:
        db.rollback()
        db.refresh(payment)
        return False
    user = db.get(User, payment.user_id)
    sub = subscriptions.ensure_subscription(db, user)
    days = get_settings().plan_billing_days
    renewing = sub.plan_id == payment.plan_id and sub.status == "ACTIVE" and sub.expires_at is not None and sub.expires_at > _now()
    base = sub.expires_at if renewing else _now()
    if not renewing:
        sub.started_at = _now()
    sub.plan_id, sub.status = payment.plan_id, "ACTIVE"
    sub.expires_at, sub.payment_provider = base + timedelta(days=days), payment.provider
    sub.provider_subscription_id = payment.provider_order_id
    try:
        db.commit()
    except IntegrityError:                                   # the same gateway payment id already belongs to another order
        db.rollback()
        raise AppError("This payment has already been used.", 409, "payment_reused")
    db.refresh(payment)
    log.info("subscription activated: user=%s plan=%s", payment.user_id, payment.plan_id)
    return True


def confirm_payment(db: Session, user: User, order_id: str, payment_id: str, signature: str) -> dict:
    payment = _own_payment(db, user, order_id)
    provider = get_payment_provider()
    if not provider.verify_payment(order_id, payment_id, signature):
        raise AppError("We couldn't verify this payment, so your plan was not changed.", 400, "invalid_signature")
    first_time = _activate(db, payment, payment_id)
    return {"status": payment.status, "already_processed": not first_time, "plan_id": payment.plan_id}


def cancel_checkout(db: Session, user: User, order_id: str, reason: str) -> dict:
    """The user closed Checkout or a payment attempt failed. A payment that is already PAID is never touched."""
    payment = _own_payment(db, user, order_id)
    _mark_unpaid(db, payment, "CANCELLED" if reason == "cancelled" else "FAILED", None if reason == "cancelled" else "Payment failed")
    return {"status": payment.status}


def _mark_unpaid(db: Session, payment: Payment, status: str, reason: str | None) -> None:
    db.execute(update(Payment).where(Payment.id == payment.id, Payment.status == "CREATED").values(status=status, failure_reason=reason))
    db.commit()
    db.refresh(payment)


def handle_webhook(db: Session, body: bytes, signature: str, headers: dict[str, str]) -> dict:
    provider = get_payment_provider()
    if not provider.verify_webhook(body, signature):
        raise AppError("Invalid webhook signature.", 400, "invalid_signature")
    event = provider.parse_webhook(body, headers)
    if event is None:
        return {"ok": True, "ignored": True}
    db.add(PaymentEventRow(event_id=event.event_id, event_type=event.kind))
    try:
        db.commit()
    except IntegrityError:                                   # this exact event was delivered before
        db.rollback()
        return {"ok": True, "duplicate": True}
    payment = db.scalars(select(Payment).where(Payment.provider_order_id == event.order_id)).first()
    if not payment:
        return {"ok": True, "ignored": True}
    if event.kind == "PAID":
        if payment.status != "PAID":
            try:
                _activate(db, payment, event.payment_id)
            except AppError:
                pass
    else:
        _mark_unpaid(db, payment, "FAILED", (event.reason or "Payment failed")[:200])
    return {"ok": True}


def user_payments(db: Session, user: User, limit: int = 20) -> list[dict]:
    rows = db.scalars(select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(limit)).all()
    return [{"order_id": p.provider_order_id, "plan_id": p.plan_id, "amount_minor": p.amount_minor, "currency": p.currency,
             "status": p.status, "paid_at": p.paid_at, "created_at": p.created_at} for p in rows]


def last_payment_status(db: Session, user_id: str) -> str | None:
    p = db.scalars(select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc())).first()
    return p.status if p else None
