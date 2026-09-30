import threading
import time

from app.db import SessionLocal
from app.models import GeneratedAsset, GenerationJob, Notification, UsageRecord
from app.services import jobs, runner
from app.services.worker import WorkerPool

from .test_generation import generate, project


def run_all():
    with SessionLocal() as db:
        while (j := jobs.claim_next(db)):
            runner.run_job(j.id)


def job_of(client, h, job_id):
    return client.get(f"/api/jobs/{job_id}", headers=h).json()


def used(client, h, gen="ai_avatar"):
    return next(i["used"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == gen)


def submit(client, h, marker="", **kw):
    r = generate(client, h, refined_prompt=f"A warrior walks. {marker}", **kw)
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def notifications(client, h):
    return client.get("/api/notifications", headers=h).json()


def test_job_completes_stores_result_in_project_and_notifies(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    jid = submit(client, h, project_id=pid)
    run_all()
    job = job_of(client, h, jid)
    assert job["status"] == "COMPLETED" and job["stage"] == "COMPLETED" and job["provider"] == "dev-simulator"
    assert job["progress"] is None and job["simulated"] is True and job["completed_at"] and job["started_at"]
    assert job["assets"][0]["type"] == "AVATAR" and job["assets"][0]["status"] == "SIMULATED"
    assets = client.get(f"/api/projects/{pid}/assets?type=avatar", headers=h).json()
    assert len(assets) == 1 and "SIMULATED OUTPUT" in assets[0]["text_preview"]
    n = notifications(client, h)
    assert n["unread"] == 1 and n["items"][0]["type"] == "job_completed" and n["items"][0]["job_id"] == jid
    assert n["items"][0]["project_id"] == pid
    assert used(client, h) == 1


def test_job_without_project_keeps_result_on_job(client, make_user):
    h, _ = make_user()
    jid = submit(client, h)
    run_all()
    job = job_of(client, h, jid)
    assert job["status"] == "COMPLETED" and job["assets"] == [] and "SIMULATED" in job["output"]["text"]


def test_processing_status_visible_while_running(client, make_user):
    h, _ = make_user()
    jid = submit(client, h)
    with SessionLocal() as db:
        claimed = jobs.claim_next(db)
        assert claimed.status == "PROCESSING" and claimed.stage == "PREPARING" and claimed.attempts == 1
    assert job_of(client, h, jid)["status"] == "PROCESSING"


def test_permanent_failure_keeps_quota_and_notifies_without_details(client, make_user):
    h, _ = make_user()
    jid = submit(client, h, "[simulate:fail]")
    run_all()
    job = job_of(client, h, jid)
    assert job["status"] == "FAILED" and job["error_code"] == "GENERATION_FAILED" and job["attempts"] == 1
    assert "simulated" not in job["error_message"].lower() and "Traceback" not in job["error_message"]
    assert notifications(client, h)["items"][0]["type"] == "job_failed"
    assert used(client, h) == 1                       # provider was reached: allowance is spent


def test_invalid_request_is_not_retried_and_refunds_quota(client, make_user):
    h, _ = make_user()
    jid = submit(client, h, "[simulate:invalid]")
    run_all()
    job = job_of(client, h, jid)
    assert job["status"] == "FAILED" and job["error_code"] == "INVALID_REQUEST" and job["attempts"] == 1
    assert used(client, h) == 0


def test_provider_quota_error_is_not_retried_and_refunds(client, make_user):
    h, _ = make_user()
    jid = submit(client, h, "[simulate:noquota]")
    run_all()
    assert job_of(client, h, jid)["error_code"] == "QUOTA_EXCEEDED" and used(client, h) == 0


def test_transient_failure_retries_once_then_completes(client, make_user):
    h, _ = make_user()
    jid = submit(client, h, "[simulate:transient]")
    with SessionLocal() as db:
        runner.run_job(jobs.claim_next(db).id)
    job = job_of(client, h, jid)
    assert job["status"] == "RETRYING" and job["error_code"] == "PROVIDER_UNAVAILABLE" and job["attempts"] == 1
    assert notifications(client, h)["items"][0]["type"] == "job_retry"
    run_all()
    job = job_of(client, h, jid)
    assert job["status"] == "COMPLETED" and job["attempts"] == 2 and job["error_code"] is None
    assert used(client, h) == 1                       # one generation = one unit, however many attempts


def test_retry_is_capped_at_one(client, make_user, monkeypatch):
    from app.config import get_settings
    h, _ = make_user()
    monkeypatch.setattr(get_settings(), "job_max_auto_retries", 0)
    jid = submit(client, h, "[simulate:transient]")
    run_all()
    job = job_of(client, h, jid)
    assert job["status"] == "FAILED" and job["error_code"] == "PROVIDER_UNAVAILABLE" and job["attempts"] == 1
    assert used(client, h) == 0                       # never produced work -> allowance returned


def test_retrying_job_waits_for_its_retry_time(client, make_user, monkeypatch):
    from app.config import get_settings
    h, _ = make_user()
    monkeypatch.setattr(get_settings(), "job_retry_delay_seconds", 3600)
    jid = submit(client, h, "[simulate:transient]")
    run_all()
    assert job_of(client, h, jid)["status"] == "RETRYING"
    with SessionLocal() as db:
        assert jobs.claim_next(db) is None


def test_cancel_queued_job_refunds(client, make_user):
    h, _ = make_user()
    jid = submit(client, h)
    assert used(client, h) == 1
    r = client.post(f"/api/jobs/{jid}/cancel", headers=h)
    assert r.json()["status"] == "CANCELLED" and used(client, h) == 0
    assert client.post(f"/api/jobs/{jid}/cancel", headers=h).status_code == 409
    run_all()                                          # cancelled jobs are never picked up
    assert job_of(client, h, jid)["status"] == "CANCELLED"


def _cancel_running(client, h, marker):
    jid = submit(client, h, marker)
    with SessionLocal() as db:
        claimed = jobs.claim_next(db)
    t = threading.Thread(target=runner.run_job, args=(claimed.id,))
    t.start()
    for _ in range(50):                                # wait until the provider has really started
        time.sleep(0.05)
        if job_of(client, h, jid)["stage"] == "GENERATING":
            break
    assert client.post(f"/api/jobs/{jid}/cancel", headers=h).status_code == 200
    t.join(timeout=10)
    assert not t.is_alive()
    return job_of(client, h, jid)


def test_cancel_running_job_uses_provider_cancel_when_supported(client, make_user):
    h, _ = make_user()
    job = _cancel_running(client, h, "[simulate:slow]")
    assert job["status"] == "CANCELLED" and job["output"]["cancellation"] == "provider_cancelled"
    assert used(client, h) == 1                        # provider had started


def test_cancel_running_job_is_honest_when_provider_cannot_cancel(client, make_user):
    h, _ = make_user()
    job = _cancel_running(client, h, "[simulate:slow] [simulate:nocancel]")
    assert job["status"] == "CANCELLED" and job["output"]["cancellation"] == "stopped_locally"


def test_provider_removed_between_submit_and_run_fails_cleanly(client, make_user):
    from app.providers import registry
    h, _ = make_user()
    jid = submit(client, h)
    saved = registry._providers.pop("dev-simulator")
    try:
        run_all()
    finally:
        registry._providers["dev-simulator"] = saved
    job = job_of(client, h, jid)
    assert job["status"] == "FAILED" and job["error_code"] == "API_NOT_CONFIGURED" and used(client, h) == 0
    assert "administrator" in job["error_message"]


def test_interrupted_processing_jobs_are_requeued_on_startup(client, make_user):
    h, _ = make_user()
    jid = submit(client, h)
    with SessionLocal() as db:
        jobs.claim_next(db)
        assert jobs.recover_interrupted(db) == 1
    assert job_of(client, h, jid)["status"] == "QUEUED"
    run_all()
    assert job_of(client, h, jid)["status"] == "COMPLETED"


def test_regenerate_creates_new_linked_job_and_keeps_original(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    first = submit(client, h, project_id=pid)
    run_all()
    r = client.post(f"/api/jobs/{first}/regenerate", headers=h)
    assert r.status_code == 201 and r.json()["job_id"] != first and r.json()["status"] == "QUEUED"
    new = job_of(client, h, r.json()["job_id"])
    old = job_of(client, h, first)
    assert new["parent_id"] == first and new["refined_prompt"] == old["refined_prompt"] and new["project_id"] == pid
    assert old["status"] == "COMPLETED"
    # edit-and-regenerate: a modified prompt goes through POST /api/generations with parent_id
    r = generate(client, h, refined_prompt="A different prompt", parent_id=first, project_id=pid)
    assert job_of(client, h, r.json()["job_id"])["parent_id"] == first
    h2, _ = make_user("b@example.com")
    assert client.post(f"/api/jobs/{first}/regenerate", headers=h2).status_code == 404


def test_manual_retry_of_failed_job_respects_quota(client, make_user):
    h, _ = make_user()
    ids = [submit(client, h, "[simulate:fail]") for _ in range(3)]
    run_all()
    assert used(client, h) == 3
    assert client.post(f"/api/jobs/{ids[0]}/regenerate", headers=h).status_code == 429


def test_delete_job_rules(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    jid = submit(client, h, project_id=pid)
    assert client.delete(f"/api/jobs/{jid}", headers=h).status_code == 409   # still queued
    run_all()
    assert client.delete(f"/api/jobs/{jid}", headers=h).status_code == 204
    assert client.get(f"/api/jobs/{jid}", headers=h).status_code == 404
    assert len(client.get(f"/api/projects/{pid}/assets", headers=h).json()) == 1   # the result stays in the project
    h2, _ = make_user("b@example.com")
    jid2 = submit(client, h, project_id=pid)
    run_all()
    assert client.delete(f"/api/jobs/{jid2}", headers=h2).status_code == 404


def test_history_filters_and_isolation(client, make_user):
    h, _ = make_user()
    h2, _ = make_user("b@example.com")
    pid = project(client, h)
    v = submit(client, h, project_id=pid)
    m = client.post("/api/generations", headers=h, json={"generator_type": "interactive_avatar", "original_prompt": "hello", "refined_prompt": "hello"}).json()["job_id"]
    run_all()
    assert {j["id"] for j in client.get("/api/jobs?type=ai_avatar", headers=h).json()} == {v}
    assert {j["id"] for j in client.get("/api/jobs?type=AVATAR,interactive_avatar", headers=h).json()} == {v, m}
    assert {j["id"] for j in client.get(f"/api/jobs?project_id={pid}", headers=h).json()} == {v}
    assert client.get("/api/jobs?status=FAILED", headers=h).json() == []
    assert client.get("/api/jobs", headers=h2).json() == []
    assert client.get(f"/api/jobs/{v}", headers=h2).status_code == 404
    assert client.post(f"/api/jobs/{v}/cancel", headers=h2).status_code == 404


def test_project_delete_removes_its_jobs_and_assets(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    submit(client, h, project_id=pid)
    run_all()
    client.delete(f"/api/projects/{pid}", headers=h)
    with SessionLocal() as db:
        assert db.query(GenerationJob).count() == 0 and db.query(GeneratedAsset).count() == 0


def test_admin_can_disable_provider_and_set_cap(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    listing = client.get("/api/admin/providers", headers=ah).json()["providers"]
    sim = next(p for p in listing if p["name"] == "dev-simulator")
    assert sim["enabled"] and sim["provider_quota"] == "unknown" and sim["simulated"]
    assert any(p["capability"] == "text" and not p["configured"] for p in listing)   # LLM shown, key never included
    client.put("/api/admin/providers/dev-simulator", json={"enabled": False}, headers=ah)
    r = generate(client, h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    client.put("/api/admin/providers/dev-simulator", json={"enabled": True, "daily_cap": 1}, headers=ah)
    assert generate(client, h).status_code == 201
    r = generate(client, h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "QUOTA_EXCEEDED"
    client.put("/api/admin/providers/dev-simulator", json={"clear_cap": True}, headers=ah)
    assert generate(client, h).status_code == 201
    assert client.put("/api/admin/providers/nope", json={"enabled": False}, headers=ah).status_code == 404


def test_notifications_read_flow(client, make_user):
    h, _ = make_user()
    submit(client, h)
    run_all()
    n = notifications(client, h)
    assert n["unread"] == 1
    client.post(f"/api/notifications/{n['items'][0]['id']}/read", headers=h)
    assert notifications(client, h)["unread"] == 0


def test_worker_pool_processes_jobs_in_background_threads(client, make_user, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "worker_poll_seconds", 0.05)
    h, _ = make_user()
    ids = [submit(client, h) for _ in range(3)]
    pool = WorkerPool()
    pool.start()
    try:
        for _ in range(100):
            if all(job_of(client, h, i)["status"] == "COMPLETED" for i in ids):
                break
            time.sleep(0.1)
    finally:
        pool.stop()
    assert [job_of(client, h, i)["status"] for i in ids] == ["COMPLETED"] * 3
    with SessionLocal() as db:
        assert db.query(Notification).count() == 3
        assert db.query(UsageRecord).filter_by(status="SUCCEEDED").count() == 3
