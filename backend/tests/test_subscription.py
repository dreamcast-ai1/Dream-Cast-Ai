import dataclasses
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import plans as plans_module
from app.db import SessionLocal
from app.models import AppSetting, Subscription, User
from app.services import subscriptions, usage

from .helpers import generate, make_png, make_project, upload_ref

BACKEND = Path(__file__).resolve().parents[1]


def current(client, h):
    return client.get("/api/subscription/current", headers=h).json()


def video(client, h, **kw):
    return generate(client, h, "video", prompt="A knight rides.", options=kw.pop("options", {"duration_seconds": 10, "aspect_ratio": "16:9"}), **kw)


def set_plan(client, admin_h, user_id, plan_id, days=None):
    return client.patch(f"/api/admin/users/{user_id}/subscription", headers=admin_h, json={"plan_id": plan_id, "days": days})


def patch_plan(monkeypatch, plan_id="teaser", **changes):
    """Swap a plan for a modified copy (plans are frozen config), e.g. to test entitlement checks."""
    p = plans_module.PLANS[plan_id]
    features = {**p.features, **changes.pop("features", {})}
    monkeypatch.setitem(plans_module.PLANS, plan_id, dataclasses.replace(p, features=features, **changes))


pytestmark = pytest.mark.usefixtures("all_features", "advanced_options")


# ------------------------------------------------------------------ plans and default subscription
def test_plan_catalogue_is_public_and_uses_movie_names(client):
    r = client.get("/api/subscription/plans")                      # no authentication needed
    assert r.status_code == 200 and r.json()["payments_enabled"] is False        # no Razorpay keys in tests
    ps = r.json()["plans"]
    assert [p["name"] for p in ps] == ["Teaser", "Trailer", "Movie"]
    assert [p["id"] for p in ps] == ["teaser", "trailer", "movie"]
    assert [p["tagline"] for p in ps] == ["A first look", "Start making your own films", "For serious production"]
    assert [p["price_minor"] for p in ps] == [0, 19900, 49900] and all(p["currency"] == "INR" for p in ps)
    assert ps[0]["billing_period"] == "free" and all(p["billing_period"] == "month" for p in ps[1:])


def test_prices_come_from_settings_not_code(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "plan_trailer_price_inr", 249)
    assert plans_module._build()["trailer"].price_minor == 24900


def test_video_allowance_is_5_15_40_and_everything_else_scales_with_it(client):
    ps = {p["id"]: p for p in client.get("/api/subscription/plans").json()["plans"]}
    order = ["teaser", "trailer", "movie"]
    assert [ps[i]["limits"]["video"] for i in order] == [5, 15, 40]
    for gen in ("story", "script", "lyrics", "music", "voice", "image"):
        values = [ps[i]["limits"][gen] for i in order]
        assert values == [values[0], values[0] * 3, values[0] * 8], (gen, values)
    assert [ps[i]["price_minor"] for i in order] == [0, 19900, 49900] and ps["teaser"]["limits"]["story"] == 20
    assert all(p["features"]["max_video_seconds"] <= 15 for p in ps.values())


def test_new_user_gets_teaser_plan_automatically(client, make_user):
    h, user = make_user()
    c = current(client, h)
    assert c["plan"]["id"] == "teaser" and c["plan"]["name"] == "Teaser" and c["payments_enabled"] is False
    assert c["subscription"]["status"] == "ACTIVE" and c["subscription"]["expires_at"] is None and c["subscription"]["payment_provider"] is None
    with SessionLocal() as db:
        assert db.query(Subscription).filter_by(user_id=user["id"]).count() == 1


def test_user_created_before_subscriptions_existed_is_healed_on_first_request(client, make_user):
    h, user = make_user()
    with SessionLocal() as db:
        db.query(Subscription).delete()
        db.commit()
    assert current(client, h)["plan"]["id"] == "teaser"
    with SessionLocal() as db:
        assert db.query(Subscription).filter_by(user_id=user["id"]).count() == 1


def test_subscription_endpoints_require_login_except_plans(client):
    for url in ("/api/subscription/current", "/api/subscription/usage", "/api/subscription/entitlements"):
        assert client.get(url).status_code == 401
    assert client.post("/api/subscription/checkout").status_code == 401


def test_usage_and_entitlements_endpoints(client, make_user):
    h, _ = make_user()
    assert video(client, h).status_code == 201
    u = client.get("/api/subscription/usage", headers=h).json()
    v = next(i for i in u["items"] if i["generator"] == "video")
    assert u["plan_id"] == "teaser" and u["period"] == "month" and u["resets_at"] and (v["used"], v["limit"], v["remaining"]) == (1, 5, 4)
    assert {"story", "script", "music", "voice", "video", "face_replacement"} <= {i["generator"] for i in u["items"]}
    e = client.get("/api/subscription/entitlements", headers=h).json()
    assert e["plan_id"] == "teaser" and e["features"]["max_video_seconds"] == 15 and e["limits"]["video"] == 5
    legacy = client.get("/api/usage", headers=h).json()                  # the original endpoint keeps working
    assert legacy["period"] == "this month" and legacy["plan_name"] == "Teaser" and next(i for i in legacy["items"] if i["generator"] == "video")["remaining"] == 4



# ------------------------------------------------------------------ server-side enforcement
def test_limits_are_enforced_by_the_api_not_the_buttons(client, make_user):
    h, _ = make_user()
    assert [video(client, h).status_code for _ in range(6)] == [201] * 5 + [429]
    r = video(client, h)
    assert r.json()["error"]["code"] == "QUOTA_EXCEEDED"
    h2, _ = make_user("b@example.com")
    assert video(client, h2).status_code == 201                         # limits are per user


def test_every_tracked_generator_is_limited_per_plan(client, make_user):
    h, _ = make_user()
    codes = {}
    for gen, n in (("story", 20), ("script", 10), ("music", 10), ("voice", 20), ("video", 5), ("face_replacement", 3)):
        used = usage.get_limits  # noqa: F841 - documents that limits come from the plan service
        with SessionLocal() as db:
            uid = db.query(User).first().id
            for _ in range(n):
                usage.record(db, uid, gen)
            assert not usage.has_quota(db, uid, gen)
            codes[gen] = usage.remaining(db, uid, gen)
    assert set(codes.values()) == {0}


def test_upgrading_raises_limits_and_downgrading_restores_them(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, user = make_user()
    for _ in range(5):
        assert video(client, h).status_code == 201
    assert video(client, h).status_code == 429
    r = set_plan(client, ah, user["id"], "trailer", days=30)
    assert r.status_code == 200 and r.json()["plan_id"] == "trailer" and r.json()["expires_at"]
    c = current(client, h)
    assert c["plan"]["name"] == "Trailer" and c["subscription"]["payment_provider"] == "admin"
    assert next(i for i in client.get("/api/subscription/usage", headers=h).json()["items"] if i["generator"] == "video")["limit"] == 15
    assert video(client, h).status_code == 201                           # the bigger allowance applies immediately
    set_plan(client, ah, user["id"], "teaser")
    assert current(client, h)["subscription"]["payment_provider"] is None
    assert video(client, h).status_code == 429                           # used 6 this month, free limit is 5


def test_expired_cancelled_and_unknown_plans_fall_back_to_free(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, user = make_user()
    set_plan(client, ah, user["id"], "movie", days=30)
    with SessionLocal() as db:
        sub = db.query(Subscription).filter_by(user_id=user["id"]).one()
        sub.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    c = current(client, h)
    assert c["plan"]["id"] == "teaser" and c["subscription"]["plan_id"] == "movie" and c["subscription"]["effective_plan_id"] == "teaser"
    with SessionLocal() as db:
        sub = db.query(Subscription).filter_by(user_id=user["id"]).one()
        sub.expires_at, sub.status = None, "CANCELLED"
        db.commit()
    assert current(client, h)["plan"]["id"] == "teaser"
    with SessionLocal() as db:
        sub = db.query(Subscription).filter_by(user_id=user["id"]).one()
        sub.status = "PAST_DUE"
        db.commit()
    assert current(client, h)["plan"]["id"] == "movie"             # grace period keeps access
    with SessionLocal() as db:
        sub = db.query(Subscription).filter_by(user_id=user["id"]).one()
        sub.status, sub.plan_id = "ACTIVE", "retired_plan"
        db.commit()
    assert current(client, h)["plan"]["id"] == "teaser"                # a bad/retired plan id never locks anyone out or grants extras


def test_admin_subscription_endpoint_rules(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, user = make_user()
    assert set_plan(client, h, user["id"], "trailer").status_code == 403           # users can't grant themselves a plan
    assert set_plan(client, ah, user["id"], "platinum").status_code == 404
    assert set_plan(client, ah, "nope", "trailer").status_code == 404
    assert client.patch(f"/api/admin/users/{user['id']}/subscription", headers=ah, json={"plan_id": "trailer", "days": 0}).status_code == 422
    assert current(client, h)["plan"]["id"] == "teaser"
    users = client.get("/api/admin/users", headers=ah).json()
    assert {u["email"]: u["plan_id"] for u in users} == {"boss@example.com": "teaser", "user@example.com": "teaser"}


def test_admin_limits_are_per_plan_and_legacy_overrides_still_apply(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    with SessionLocal() as db:                                            # an override saved before plans existed
        db.add(AppSetting(key="daily_limits", value={"video": 7}))
        db.commit()
    assert next(i for i in client.get("/api/admin/limits", headers=ah).json()["items"] if i["generator"] == "video")["limit"] == 7
    r = client.put("/api/admin/limits", headers=ah, json={"plan": "trailer", "limits": {"video": 99}}).json()
    assert r["plan"] == "trailer" and next(i for i in r["items"] if i["generator"] == "video")["limit"] == 99
    assert next(i for i in client.get("/api/admin/limits", headers=ah).json()["items"] if i["generator"] == "video")["limit"] == 7
    assert [p["id"] for p in client.get("/api/admin/limits", headers=ah).json()["plans"]] == ["teaser", "trailer", "movie"]
    assert client.get("/api/admin/limits?plan=nope", headers=ah).status_code == 404
    assert client.put("/api/admin/limits", headers=ah, json={"plan": "nope", "limits": {"video": 1}}).status_code == 404
    assert client.put("/api/admin/limits", headers=h, json={"limits": {"video": 99}}).status_code == 403
    assert next(i for i in client.get("/api/subscription/plans").json()["plans"] if i["id"] == "trailer")["limits"]["video"] == 99


# ------------------------------------------------------------------ entitlements: video length and features
def test_plan_video_cap_is_enforced_on_refine_and_submit(client, make_user, monkeypatch):
    patch_plan(monkeypatch, features={"max_video_seconds": 10})
    h, _ = make_user()
    body = {"generator_type": "video", "prompt": "A knight rides. Make it 15 seconds.", "options": {}}
    r = client.post("/api/generate/refine", headers=h, json=body).json()
    assert r["metadata"]["options"]["duration_seconds"] == 10 and any("Your plan allows videos up to 10 seconds" in w for w in r["metadata"]["warnings"])
    bad = video(client, h, options={"duration_seconds": 15, "aspect_ratio": "16:9"})
    assert bad.status_code == 422 and "Your plan allows videos up to 10 seconds" in bad.json()["error"]["message"]
    assert video(client, h, options={"duration_seconds": 10, "aspect_ratio": "16:9"}).status_code == 201


def test_no_plan_can_exceed_the_global_15_second_cap():
    p = plans_module.Plan("x", "X", "", "", 0, "INR", "free", {}, {"max_video_seconds": 120})
    assert p.features["max_video_seconds"] == 15
    for plan in plans_module.PLANS.values():
        assert plan.features["max_video_seconds"] <= 15


def test_sixty_and_hundred_second_requests_never_reach_a_provider(client, make_user):
    h, _ = make_user()
    for secs in (60, 100, 120):
        r = client.post("/api/generate/refine", headers=h, json={"generator_type": "video", "prompt": f"A knight rides. Make it {secs} seconds.", "options": {}}).json()
        assert r["metadata"]["options"]["duration_seconds"] == 15
        assert video(client, h, options={"duration_seconds": secs}).status_code == 422


def test_feature_entitlements_gate_image_to_video_and_face_replacement(client, make_user, monkeypatch):
    patch_plan(monkeypatch, features={"image_to_video": False, "face_replacement": False})
    h, _ = make_user()
    pid = make_project(client, h)
    ref = upload_ref(client, h, pid, make_png(80, 80, 3))
    i2v = video(client, h, project_id=pid, reference_assets=[ref["id"]], options={"method": "Image to Video", "duration_seconds": 10})
    assert i2v.status_code == 403 and i2v.json()["error"]["code"] == "plan_feature" and "Image-to-video" in i2v.json()["error"]["message"]
    face = client.post("/api/generations", headers=h, json={"generator_type": "face_replacement", "original_prompt": "", "refined_prompt": "swap", "project_id": pid})
    assert face.status_code == 403 and "Face replacement" in face.json()["error"]["message"]
    assert video(client, h, project_id=pid).status_code == 201           # plain text-to-video is still allowed


# ------------------------------------------------------------------ usage periods
def test_usage_windows():
    day, month = usage.window_start("day"), usage.window_start("month")
    assert day.hour == day.minute == 0 and month.day == 1 and month <= day
    assert usage.window_end("day") - day == timedelta(days=1)
    end = usage.window_end("month")
    assert end.day == 1 and end > day and (end - month).days in (28, 29, 30, 31)


def test_monthly_plan_counts_the_whole_month(client, make_user, monkeypatch):
    patch_plan(monkeypatch, usage_period="month")
    h, user = make_user()
    with SessionLocal() as db:
        from app.models import UsageRecord
        yesterday = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
        first_of_month = usage.window_start("month")
        for _ in range(5):
            db.add(UsageRecord(user_id=user["id"], generator_type="video", status="SUCCEEDED", request_count=1,
                               created_at=max(first_of_month, yesterday)))
        db.commit()
        if yesterday >= first_of_month:                                    # skip the check on the 1st of a month
            assert not usage.has_quota(db, user["id"], "video")
    u = client.get("/api/subscription/usage", headers=h).json()
    assert u["period"] == "month"


# ------------------------------------------------------------------ migration
def test_migration_gives_every_existing_user_the_free_plan(tmp_path):
    db_file = tmp_path / "old.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}", "APP_ENV": "development"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "alembic", *a], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert run("upgrade", "0004").returncode == 0                          # the database as it was before this phase
    con = sqlite3.connect(db_file)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(3):
        con.execute("INSERT INTO users (id, email, name, auth_provider, role, is_active, created_at) VALUES (?, ?, 'N', 'local', 'USER', 1, ?)",
                    (f"u{i}", f"u{i}@example.com", now))
    con.commit()
    con.close()
    r = run("upgrade", "head")
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(db_file)
    rows = con.execute("SELECT user_id, plan_id, status FROM subscriptions ORDER BY user_id").fetchall()
    assert rows == [("u0", "teaser", "ACTIVE"), ("u1", "teaser", "ACTIVE"), ("u2", "teaser", "ACTIVE")]
    assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 3        # nothing was lost
    assert run("upgrade", "head").returncode == 0                              # re-running is safe
    assert run("check").returncode == 0 and "No new upgrade operations" in (run("check").stdout + run("check").stderr)
