"""Razorpay checkout, signature verification, webhooks and authorization. The gateway's HTTP call is replaced; the signature
code under test is the real one. No network, no real keys."""
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import plans as plans_module
from app.config import get_settings
from app.db import SessionLocal
from app.models import Payment, Subscription
from app.payments import razorpay, set_payment_provider

KEY_SECRET, WEBHOOK_SECRET = "rzp_test_secret_value_123", "whsec_test_value_456"
BACKEND = Path(__file__).resolve().parents[1]


class FakeRazorpay(razorpay.RazorpayPaymentProvider):
    def __init__(self):
        self.orders = []

    def _post_order(self, payload):
        self.orders.append(payload)
        return {"id": f"order_test{len(self.orders)}", "amount": payload["amount"], "currency": payload["currency"]}


@pytest.fixture
def gateway(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_public_id")
    monkeypatch.setattr(s, "razorpay_key_secret", KEY_SECRET)
    monkeypatch.setattr(s, "razorpay_webhook_secret", WEBHOOK_SECRET)
    fake = FakeRazorpay()
    set_payment_provider(fake)
    yield fake
    set_payment_provider(None)


def sign(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def checkout(client, h, plan_id="trailer"):
    return client.post("/api/subscription/checkout", headers=h, json={"plan_id": plan_id})


def verify(client, h, order_id, payment_id="pay_1", signature=None):
    return client.post("/api/subscription/verify", headers=h, json={
        "razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
        "razorpay_signature": signature if signature is not None else sign(order_id, payment_id)})


def current(client, h):
    return client.get("/api/subscription/current", headers=h).json()


def webhook(client, event, order_id, payment_id="pay_1", event_id="evt_1", secret=WEBHOOK_SECRET, signature=None, **extra):
    body = json.dumps({"event": event, "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, **extra}}}}).encode()
    sig = signature if signature is not None else hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/payments/razorpay/webhook", content=body, headers={"x-razorpay-signature": sig, "x-razorpay-event-id": event_id,
                                                                               "content-type": "application/json"})


def video_limit(client, h):
    return next(i for i in client.get("/api/subscription/usage", headers=h).json()["items"] if i["generator"] == "video")["limit"]


# ------------------------------------------------------------------ order creation
def test_new_user_starts_on_free_teaser_without_payment(client, make_user):
    h, _ = make_user()
    c = current(client, h)
    assert c["plan"]["id"] == "teaser" and c["plan"]["price_minor"] == 0 and c["subscription"]["status"] == "ACTIVE"
    assert client.get("/api/subscription/usage", headers=h).status_code == 200      # works without any payment setup


def test_upgrade_order_is_created_with_the_server_price_and_changes_nothing(client, make_user, gateway):
    h, _ = make_user()
    r = checkout(client, h, "trailer")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["order_id"] == "order_test1" and d["amount"] == 19900 and d["currency"] == "INR" and d["key_id"] == "rzp_test_public_id"
    assert d["plan"] == {"id": "trailer", "name": "Trailer"}
    assert gateway.orders[0]["amount"] == 19900 and gateway.orders[0]["notes"]["plan_id"] == "trailer"
    assert current(client, h)["plan"]["id"] == "teaser"                 # an order is not a purchase
    with SessionLocal() as db:
        [p] = db.query(Payment).all()
        assert (p.status, p.plan_id, p.amount_minor) == ("CREATED", "trailer", 19900)
    assert KEY_SECRET not in r.text and WEBHOOK_SECRET not in r.text


def test_the_browser_cannot_choose_the_price(client, make_user, gateway):
    h, _ = make_user()
    r = client.post("/api/subscription/checkout", headers=h, json={"plan_id": "movie", "amount": 1, "price_minor": 1})
    assert r.status_code == 200 and r.json()["amount"] == 49900 and gateway.orders[0]["amount"] == 49900


@pytest.mark.parametrize("plan_id", ["teaser", "studio", "nope", ""])
def test_only_real_paid_plans_can_be_ordered(client, make_user, gateway, plan_id):
    h, _ = make_user()
    assert checkout(client, h, plan_id).status_code == 422 and not gateway.orders


def test_checkout_requires_login_and_configuration(client, make_user, monkeypatch):
    assert client.post("/api/subscription/checkout", json={"plan_id": "trailer"}).status_code == 401
    h, _ = make_user()
    set_payment_provider(None)
    r = checkout(client, h)                                               # no keys configured in the test environment
    assert r.status_code == 503 and r.json()["error"]["code"] == "payments_not_configured"
    assert current(client, h)["payments_enabled"] is False


def test_checkout_is_rate_limited(client, make_user, gateway, monkeypatch):
    from app.routers import subscription
    monkeypatch.setattr(get_settings(), "checkout_rate_limit_per_minute", 3)
    subscription.checkout_rate_limit.reset()
    h, _ = make_user()
    codes = [checkout(client, h).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    subscription.checkout_rate_limit.reset()


def test_gateway_errors_are_reported_cleanly(client, make_user, gateway, monkeypatch):
    from app.payments import PaymentProviderError

    def boom(payload):
        raise PaymentProviderError("The payment service is unreachable right now. Please try again.", "network")
    monkeypatch.setattr(gateway, "_post_order", boom)
    h, _ = make_user()
    r = checkout(client, h)
    assert r.status_code == 502 and "unreachable" in r.json()["error"]["message"]
    with SessionLocal() as db:
        assert db.query(Payment).count() == 0


# ------------------------------------------------------------------ verification
def test_invalid_signature_is_rejected_and_plan_unchanged(client, make_user, gateway):
    h, _ = make_user()
    oid = checkout(client, h).json()["order_id"]
    for bad in ("0" * 64, "", sign(oid, "pay_1", secret="wrong-secret"), sign("order_other", "pay_1")):
        r = verify(client, h, oid, "pay_1", signature=bad)
        assert r.status_code in (400, 422), r.text
    assert verify(client, h, oid, "pay_2", signature=sign(oid, "pay_1")).status_code == 400      # signature for another payment id
    assert current(client, h)["plan"]["id"] == "teaser"
    with SessionLocal() as db:
        assert db.query(Payment).one().status == "CREATED"


def test_valid_signature_activates_the_plan_and_limits_apply_immediately(client, make_user, gateway):
    h, _ = make_user()
    assert video_limit(client, h) == 5
    oid = checkout(client, h).json()["order_id"]
    r = verify(client, h, oid)
    assert r.status_code == 200 and r.json() == {"status": "PAID", "already_processed": False, "plan_id": "trailer"}
    c = current(client, h)
    assert c["plan"]["id"] == "trailer" and c["subscription"]["status"] == "ACTIVE" and c["subscription"]["payment_provider"] == "razorpay"
    exp = datetime.fromisoformat(c["subscription"]["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=29) < exp - datetime.now(timezone.utc) <= timedelta(days=30, minutes=1)
    assert video_limit(client, h) == 15                                   # Trailer: 15 videos a month, immediately
    pays = client.get("/api/subscription/payments", headers=h).json()["items"]
    assert [(p["status"], p["plan_id"], p["amount_minor"]) for p in pays] == [("PAID", "trailer", 19900)]


def test_duplicate_confirmation_does_not_duplicate_or_extend(client, make_user, gateway):
    h, user = make_user()
    oid = checkout(client, h).json()["order_id"]
    assert verify(client, h, oid).json()["already_processed"] is False
    first = current(client, h)["subscription"]
    for _ in range(3):
        r = verify(client, h, oid)
        assert r.status_code == 200 and r.json()["already_processed"] is True
    assert current(client, h)["subscription"]["expires_at"] == first["expires_at"]
    with SessionLocal() as db:
        assert db.query(Subscription).filter_by(user_id=user["id"]).count() == 1 and db.query(Payment).count() == 1


def test_buying_the_same_plan_again_right_away_is_refused(client, make_user, gateway):
    h, _ = make_user()
    verify(client, h, checkout(client, h).json()["order_id"])
    r = checkout(client, h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_subscribed" and len(gateway.orders) == 1


def test_upgrading_to_a_higher_plan_and_renewing_early(client, make_user, gateway):
    h, user = make_user()
    verify(client, h, checkout(client, h, "trailer").json()["order_id"])
    verify(client, h, checkout(client, h, "movie").json()["order_id"], "pay_2")
    assert current(client, h)["plan"]["id"] == "movie" and video_limit(client, h) == 40
    with SessionLocal() as db:                                            # near expiry: renewing the same plan extends from the old expiry
        sub = db.query(Subscription).one()
        sub.expires_at = datetime.now(timezone.utc) + timedelta(days=3)
        old = sub.expires_at
        db.commit()
    verify(client, h, checkout(client, h, "movie").json()["order_id"], "pay_3")
    with SessionLocal() as db:
        assert db.query(Subscription).one().expires_at - old == timedelta(days=30)


def test_failed_or_cancelled_payment_never_activates_a_plan(client, make_user, gateway):
    h, _ = make_user()
    oid = checkout(client, h).json()["order_id"]
    assert client.post("/api/subscription/checkout/cancel", headers=h, json={"order_id": oid, "reason": "cancelled"}).json() == {"status": "CANCELLED"}
    assert current(client, h)["plan"]["id"] == "teaser"
    oid2 = checkout(client, h).json()["order_id"]
    assert client.post("/api/subscription/checkout/cancel", headers=h, json={"order_id": oid2, "reason": "failed"}).json() == {"status": "FAILED"}
    assert current(client, h)["plan"]["id"] == "teaser"
    assert client.post("/api/subscription/checkout/cancel", headers=h, json={"order_id": oid2, "reason": "bogus"}).status_code == 422
    assert verify(client, h, oid2, "pay_9").status_code == 200            # a retried payment that really succeeds still works
    assert current(client, h)["plan"]["id"] == "trailer"


def test_cancelling_after_payment_does_not_undo_it(client, make_user, gateway):
    h, _ = make_user()
    oid = checkout(client, h).json()["order_id"]
    verify(client, h, oid)
    assert client.post("/api/subscription/checkout/cancel", headers=h, json={"order_id": oid, "reason": "failed"}).json() == {"status": "PAID"}
    assert current(client, h)["plan"]["id"] == "trailer"


# ------------------------------------------------------------------ authorization
def test_users_cannot_change_plans_by_editing_requests(client, make_user, gateway):
    h, user = make_user()
    admin_url = f"/api/admin/users/{user['id']}/subscription"
    assert client.patch(admin_url, headers=h, json={"plan_id": "movie"}).status_code == 403
    assert client.get("/api/admin/users", headers=h).status_code == 403
    for method in ("put", "patch", "post"):
        r = getattr(client, method)("/api/subscription/current", headers=h, json={"plan_id": "movie"})
        assert r.status_code in (404, 405)
    assert client.post("/api/subscription/verify", headers=h, json={"razorpay_order_id": "x", "razorpay_payment_id": "y", "razorpay_signature": "z"}).status_code == 404
    assert client.patch(f"/api/auth/me", headers=h, json={"plan_id": "movie", "role": "ADMIN"}).status_code in (200, 404, 405, 422)
    assert current(client, h)["plan"]["id"] == "teaser"
    assert client.get("/api/auth/me", headers=h).json()["role"] == "USER"


def test_one_user_cannot_use_or_see_another_users_payment(client, make_user, gateway):
    ha, _ = make_user("a@example.com")
    hb, _ = make_user("b@example.com")
    oid = checkout(client, ha).json()["order_id"]
    assert verify(client, hb, oid).status_code == 404                     # even with a genuine signature
    assert client.post("/api/subscription/checkout/cancel", headers=hb, json={"order_id": oid}).status_code == 404
    assert client.get("/api/subscription/payments", headers=hb).json()["items"] == []
    assert current(client, hb)["plan"]["id"] == "teaser"
    assert verify(client, ha, oid).status_code == 200
    assert current(client, hb)["plan"]["id"] == "teaser"


def test_a_payment_id_cannot_be_reused_for_a_second_order(client, make_user, gateway):
    h, _ = make_user()
    o1 = checkout(client, h, "trailer").json()["order_id"]
    verify(client, h, o1, "pay_same")
    o2 = checkout(client, h, "movie").json()["order_id"]
    r = verify(client, h, o2, "pay_same")                                 # genuine signature for o2, but the payment id is already used
    assert r.status_code == 409 and current(client, h)["plan"]["id"] == "trailer"


# ------------------------------------------------------------------ webhooks
def test_webhook_needs_a_valid_signature(client, make_user, gateway):
    h, _ = make_user()
    oid = checkout(client, h).json()["order_id"]
    assert webhook(client, "payment.captured", oid, signature="0" * 64).status_code == 400
    assert webhook(client, "payment.captured", oid, signature="").status_code == 400
    assert webhook(client, "payment.captured", oid, secret="other").status_code == 400
    assert current(client, h)["plan"]["id"] == "teaser"


def test_webhook_without_a_configured_secret_accepts_nothing(client, make_user, gateway, monkeypatch):
    monkeypatch.setattr(get_settings(), "razorpay_webhook_secret", "")
    h, _ = make_user()
    oid = checkout(client, h).json()["order_id"]
    assert webhook(client, "payment.captured", oid, secret="").status_code == 400
    assert webhook(client, "payment.captured", oid, secret="anything").status_code == 400


def test_webhook_activates_once_and_repeats_are_harmless(client, make_user, gateway):
    h, user = make_user()
    oid = checkout(client, h).json()["order_id"]
    assert webhook(client, "payment.captured", oid).json() == {"ok": True}
    first = current(client, h)["subscription"]
    assert current(client, h)["plan"]["id"] == "trailer"
    assert webhook(client, "payment.captured", oid).json() == {"ok": True, "duplicate": True}            # same event delivered again
    assert webhook(client, "order.paid", oid, event_id="evt_2").json() == {"ok": True}                     # another event, same payment
    assert verify(client, h, oid).json()["already_processed"] is True                                      # and the browser callback too
    assert current(client, h)["subscription"]["expires_at"] == first["expires_at"]
    with SessionLocal() as db:
        assert db.query(Subscription).filter_by(user_id=user["id"]).count() == 1


def test_webhook_failure_marks_failed_but_never_downgrades_a_paid_order(client, make_user, gateway):
    h, _ = make_user()
    oid = checkout(client, h).json()["order_id"]
    webhook(client, "payment.failed", oid, event_id="evt_f", error_description="Card declined")
    with SessionLocal() as db:
        p = db.query(Payment).one()
        assert p.status == "FAILED" and p.failure_reason == "Card declined"
    assert current(client, h)["plan"]["id"] == "teaser"
    webhook(client, "payment.captured", oid, event_id="evt_p")             # the customer retried and paid
    assert current(client, h)["plan"]["id"] == "trailer"
    webhook(client, "payment.failed", oid, payment_id="pay_x", event_id="evt_late")
    assert current(client, h)["plan"]["id"] == "trailer"


def test_webhook_ignores_unknown_events_and_orders(client, make_user, gateway):
    assert webhook(client, "refund.created", "order_x").json() == {"ok": True, "ignored": True}
    assert webhook(client, "payment.captured", "order_unknown").json() == {"ok": True, "ignored": True}


# ------------------------------------------------------------------ secrets and admin
def test_secrets_never_appear_in_responses_or_logs(client, make_user, gateway, caplog):
    caplog.set_level(logging.DEBUG)
    ah, _ = make_user("boss@example.com")
    h, user = make_user()
    texts = []
    r = checkout(client, h)
    oid = r.json()["order_id"]
    texts += [r.text, verify(client, h, oid, signature="bad").text, verify(client, h, oid).text, current(client, h).__str__(),
              client.get("/api/subscription/payments", headers=h).text, client.get("/api/subscription/plans").text,
              client.get("/api/admin/users", headers=ah).text, client.get("/api/admin/providers", headers=ah).text,
              webhook(client, "payment.captured", oid, signature="0" * 64).text]
    blob = "\n".join(texts) + caplog.text
    assert KEY_SECRET not in blob and WEBHOOK_SECRET not in blob


def test_admin_sees_plan_status_usage_and_payment_and_can_grant_a_plan(client, make_user, gateway):
    ah, _ = make_user("boss@example.com")
    h, user = make_user()
    verify(client, h, checkout(client, h).json()["order_id"])
    row = next(u for u in client.get("/api/admin/users", headers=ah).json() if u["id"] == user["id"])
    assert row["plan_id"] == "trailer" and row["subscription_status"] == "ACTIVE" and row["payment_status"] == "PAID" and row["used_today"] == 0
    other = next(u for u in client.get("/api/admin/users", headers=ah).json() if u["email"] == "boss@example.com")
    assert other["plan_id"] == "teaser" and other["payment_status"] is None
    r = client.patch(f"/api/admin/users/{other['id']}/subscription", headers=ah, json={"plan_id": "movie", "days": 5})
    assert r.status_code == 200 and r.json()["plan_id"] == "movie"


# ------------------------------------------------------------------ plan ids and migration
def test_legacy_plan_ids_map_to_the_new_plans():
    assert [plans_module.get_plan(i).id for i in ("audience", "indie_director", "studio", "movie", "nonsense", None)] == \
        ["teaser", "trailer", "movie", "movie", "teaser", "teaser"]


def test_migration_renames_old_plans_and_keeps_users(tmp_path):
    db_file = tmp_path / "old.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}", "APP_ENV": "development"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "alembic", *a], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert run("upgrade", "0005").returncode == 0
    con = sqlite3.connect(db_file)
    now = datetime.now(timezone.utc).isoformat()
    for i, plan in enumerate(["audience", "indie_director", "studio", "movie"]):
        con.execute("INSERT INTO users (id, email, name, auth_provider, role, is_active, created_at) VALUES (?, ?, 'N', 'local', 'USER', 1, ?)", (f"u{i}", f"u{i}@example.com", now))
        con.execute("UPDATE subscriptions SET plan_id = ? WHERE user_id = ?", (plan, f"u{i}")) if con.execute("SELECT 1 FROM subscriptions WHERE user_id=?", (f"u{i}",)).fetchone() else \
            con.execute("INSERT INTO subscriptions (id, user_id, plan_id, status, started_at, created_at, updated_at) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?)", (f"s{i}", f"u{i}", plan, now, now, now))
    con.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('plan_limits', ?, ?)", (json.dumps({"indie_director": {"video": 9}}), now))
    con.commit()
    con.close()
    r = run("upgrade", "head")
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(db_file)
    assert dict(con.execute("SELECT user_id, plan_id FROM subscriptions").fetchall()) == {"u0": "teaser", "u1": "trailer", "u2": "movie", "u3": "movie"}
    assert con.execute("SELECT count(*) FROM app_settings WHERE key='plan_limits'").fetchone()[0] == 0     # old per-day overrides no longer describe a plan
    assert {t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"payments", "payment_events", "scenes"}
    assert run("downgrade", "0005").returncode == 0 and run("upgrade", "head").returncode == 0
    assert "No new upgrade operations" in (run("check").stdout + run("check").stderr)
