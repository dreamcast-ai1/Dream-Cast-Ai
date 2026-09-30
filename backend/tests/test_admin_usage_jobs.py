from app.db import SessionLocal
from app.providers import ErrorCode, ProviderError
from app.services import jobs, usage


def test_admin_routes_protected_on_backend(client, make_user):
    h, _ = make_user()
    assert client.put("/api/admin/providers/dev-simulator", json={"enabled": False}, headers=h).status_code == 403
    for url in ["/api/admin/stats", "/api/admin/users", "/api/admin/limits", "/api/admin/jobs", "/api/admin/providers"]:
        assert client.get(url, headers=h).status_code == 403, url
        assert client.get(url).status_code == 401, url
    assert client.put("/api/admin/limits", json={"limits": {"video": 99}}, headers=h).status_code == 403


def test_admin_stats_users_limits(client, make_user):
    ah, admin = make_user("boss@example.com")
    make_user()
    assert client.get("/api/admin/stats", headers=ah).json()["total_users"] == 2
    assert len(client.get("/api/admin/users", headers=ah).json()) == 2
    assert client.patch(f"/api/admin/users/{admin['id']}", json={"is_active": False}, headers=ah).status_code == 400
    r = client.put("/api/admin/limits", json={"limits": {"video": 7}}, headers=ah).json()
    assert next(i for i in r["items"] if i["generator"] == "video")["limit"] == 7
    uh, _ = make_user("c@example.com")
    assert next(i for i in client.get("/api/usage", headers=uh).json()["items"] if i["generator"] == "video")["limit"] == 7


def test_usage_defaults_and_quota(client, make_user):
    h, user = make_user()
    items = {i["generator"]: i for i in client.get("/api/usage", headers=h).json()["items"]}
    assert items["video"]["limit"] == 3 and items["video"]["used"] == 0 and len(items) == 9
    limits = {k: v["limit"] for k, v in items.items()}
    assert limits == {"video": 3, "music": 3, "voice": 5, "lyrics": 5, "story": 5, "script": 3, "face_replacement": 3,
                      "ai_avatar": 3, "interactive_avatar": 12}
    with SessionLocal() as db:
        for _ in range(3):
            usage.record(db, user["id"], "video", provider="x")
        assert not usage.has_quota(db, user["id"], "video")
        assert usage.has_quota(db, user["id"], "music")
    assert {i["generator"]: i for i in client.get("/api/usage", headers=h).json()["items"]}["video"]["used"] == 3


def test_jobs_and_notifications_isolated(client, make_user):
    h1, u1 = make_user("a@example.com")
    h2, _ = make_user("b@example.com")
    with SessionLocal() as db:
        jid = jobs.create_job(db, u1["id"], "story").id
        from app.services.notifications import notify
        n = notify(db, u1["id"], "hi")
    assert client.get(f"/api/jobs/{jid}", headers=h2).status_code == 404
    assert client.get("/api/jobs", headers=h2).json() == []
    assert client.post(f"/api/notifications/{n.id}/read", headers=h2).status_code == 404
    assert client.get("/api/notifications", headers=h2).json()["items"] == []


def test_admin_sees_failed_jobs(client, make_user):
    ah, _ = make_user("boss@example.com")
    _, u = make_user()
    with SessionLocal() as db:
        jobs.fail(db, jobs.create_job(db, u["id"], "voice"), ProviderError(ErrorCode.GENERATION_FAILED, "boom"))
    r = client.get("/api/admin/jobs?status=FAILED", headers=ah).json()
    assert len(r) == 1 and r[0]["error_code"] == "GENERATION_FAILED" and "boom" not in r[0]["error_message"]


def test_unhandled_errors_do_not_leak(client, make_user):
    h, _ = make_user()
    r = client.get("/api/generators", headers=h)
    assert r.status_code == 200 and len(r.json()) == 9
    assert client.get("/api/nope", headers=h).json()["error"]["message"]
