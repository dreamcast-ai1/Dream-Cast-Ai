"""Realistic end-to-end persistence: a user does everything, the backend 'restarts' (every in-memory object is dropped and rebuilt), and all
of it is still there. All records live in the database; all files live in the storage (local folder or S3-compatible bucket).
Email, payment gateway and fal.ai are mocks (MOCK TEST ONLY)."""
import re

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app
from app.models import EmailVerification, GeneratedAsset, GenerationJob, Payment, Project, Scene, Subscription, UsageRecord, User
from app.providers import register_default_providers
from app.security import auth_rate_limit, login_failure_limit, otp_send_limit, otp_verify_limit, reset_send_limit
from app.storage import get_storage

from .helpers import FakeFal, make_project, run_all, use_fal
from .test_auth_flows import login, mail, register, verify  # noqa: F401  (mail is a fixture)
from .test_billing import checkout, gateway, verify as verify_payment  # noqa: F401  (gateway is a fixture)
from .test_object_storage import fake_s3, s3_app  # noqa: F401  (fixtures)

pytestmark = pytest.mark.usefixtures("all_features")

PW = "correct-horse-1"


def restart_backend() -> TestClient:
    """What a redeploy/restart does to the process: connections, caches and registries are gone; only the database and the storage remain."""
    engine.dispose()
    get_storage.cache_clear()
    for limiter in (auth_rate_limit, login_failure_limit, otp_send_limit, otp_verify_limit, reset_send_limit):
        limiter.reset()
    register_default_providers()
    return TestClient(app)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def usage(client, h) -> dict:
    return {i["generator"]: i["used"] for i in client.get("/api/usage", headers=h).json()["items"]}


def full_flow(client, mail, gateway, monkeypatch):
    email = "persist@example.com"
    # 1-3  sign up, verify the email, sign in
    assert register(client, email, PW, "Persist").json()["verification_required"] is True
    code = re.search(r"code is (\d{6})\.", mail.to(email)[-1][2]).group(1)
    assert login(client, email, PW).status_code == 403                                   # not verified yet
    token = verify(client, email, code).json()["access_token"]
    token = login(client, email, PW).json()["access_token"]
    h = auth(token)
    # 4-5  project and scenes
    pid = make_project(client, h, "Persistent Film")
    scenes = [client.post(f"/api/projects/{pid}/scenes", headers=h, json={"title": t, "visual_prompt": f"Shot of {t}"}).json() for t in ("Dawn", "Dusk")]
    # 6  an image
    use_fal(monkeypatch, FakeFal(result_kind="image"))
    img = client.post("/api/generations", headers=h, json={"generator_type": "image", "original_prompt": "A city at sunset", "refined_prompt": "A city at sunset",
                                                          "options": {"aspect_ratio": "16:9"}, "project_id": pid}).json()["job_id"]
    run_all()
    # 7  videos for both scenes (real worker pipeline against the fal.ai mock), then 8  the movie
    use_fal(monkeypatch, FakeFal())
    for s in scenes:
        assert client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-video", headers=h).status_code == 201
    run_all()
    assert client.post(f"/api/projects/{pid}/movie/assemble", headers=h).status_code == 202
    run_all()
    # 9  a subscription paid through the (mock) gateway
    order = checkout(client, h, "trailer").json()["order_id"]
    assert verify_payment(client, h, order, "pay_persist").status_code == 200
    snapshot = {"usage": usage(client, h), "library": [(i["id"], i["type"], i["title"], i["is_movie"]) for i in client.get("/api/assets", headers=h).json()]}
    assert snapshot["usage"]["video"] == 2 and snapshot["usage"]["image"] == 1 and snapshot["usage"].get("face_replacement", 0) == 0     # the movie cost nothing
    return email, pid, [s["id"] for s in scenes], img, snapshot


def check_after_restart(c2, email, pid, scene_ids, img_job, snapshot, other_headers):
    # 10-12  logged out, backend restarted, sign in again
    assert c2.get("/api/projects").status_code == 401
    r = login(c2, email, PW)
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True
    h = auth(r.json()["access_token"])
    # 13  every record is still there
    assert [p["title"] for p in c2.get("/api/projects", headers=h).json()] == ["Persistent Film"]
    scenes = c2.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"]
    assert [(s["title"], s["status"]) for s in scenes] == [("Dawn", "READY"), ("Dusk", "READY")] and [s["id"] for s in scenes] == scene_ids
    sub = c2.get("/api/subscription/current", headers=h).json()
    assert sub["plan"]["id"] == "trailer" and sub["subscription"]["status"] == "ACTIVE" and sub["subscription"]["payment_provider"] == "razorpay"
    pays = c2.get("/api/subscription/payments", headers=h).json()["items"]
    assert [(p["status"], p["plan_id"], p["amount_minor"]) for p in pays] == [("PAID", "trailer", 19900)]
    assert usage(c2, h) == snapshot["usage"]
    jobs = c2.get("/api/jobs?limit=50", headers=h).json()
    assert sorted(j["type"] for j in jobs) == ["image", "movie", "video", "video"] and all(j["status"] == "COMPLETED" for j in jobs)
    assert any(j["id"] == img_job and j["provider"] == "fal-image" for j in jobs)
    # 14  the Library
    lib = [(i["id"], i["type"], i["title"], i["is_movie"]) for i in c2.get("/api/assets", headers=h).json()]
    assert lib == snapshot["library"] and {t for _, t, _, _ in lib} == {"IMAGE", "VIDEO"} and sum(1 for *_, m in lib if m) == 1
    item = next(i for i in c2.get("/api/assets?type=IMAGE", headers=h).json())
    assert item["project_title"] == "Persistent Film" and item["has_file"] and item["thumbnail_url"] and item["provider"] == "fal-image"
    detail = c2.get(f"/api/assets/{item['id']}", headers=h).json()
    assert detail["meta"]["model"] == "fal-ai/flux/schnell" and detail["meta"]["width"] and detail["prompt"] == "A city at sunset" and detail["meta"]["provider_job_id"]
    assert c2.get(item["url"], headers=h).content[:4] == b"\x89PNG" and c2.get(item["thumbnail_url"], headers=h).content[:3] == b"\xff\xd8\xff"
    # 15  the movie still streams (with Range) and downloads
    movie = next(i for i in c2.get("/api/assets?type=VIDEO&movies=true", headers=h).json())
    url = c2.post("/api/media/stream-url", headers=h, json={"kind": "asset", "id": movie["id"]}).json()["url"]
    assert c2.get(url, headers={"Range": "bytes=0-99"}).status_code == 206 and c2.get(url).content[4:8] == b"ftyp"
    assert c2.get(f"/api/assets/{movie['id']}/download", headers=h).status_code == 200
    # 16  ownership is still correct: someone else sees none of it and can't fetch it
    assert c2.get("/api/assets", headers=other_headers).json() == [] and c2.get("/api/projects", headers=other_headers).json() == []
    for path in (f"/api/projects/{pid}", f"/api/projects/{pid}/scenes", f"/api/projects/{pid}/movie", f"/api/assets/{movie['id']}", f"/api/files/asset/{movie['id']}"):
        assert c2.get(path, headers=other_headers).status_code == 404, path
    assert c2.post("/api/media/stream-url", headers=other_headers, json={"kind": "asset", "id": movie["id"]}).status_code == 404
    assert c2.get(url).status_code == 200                                                # the owner's signed link still works after a restart
    # database-level proof: the records are rows, not memory
    with SessionLocal() as db:
        assert (u := db.query(User).filter_by(email=email).one()).email_verified is True and db.query(EmailVerification).filter_by(user_id=u.id).count() == 1
        assert db.query(Project).count() == 1 and db.query(Scene).count() == 2 and db.query(GeneratedAsset).count() == 4 and db.query(GenerationJob).count() == 4
        assert db.query(Subscription).filter_by(plan_id="trailer").count() == 1 and db.query(Payment).filter_by(status="PAID").count() == 1
        files = db.query(GeneratedAsset).filter(GeneratedAsset.file_path.isnot(None)).all()
        assert len(files) == 4 and all(get_storage().exists(a.file_path) and a.user_id for a in files)
        assert db.query(UsageRecord).filter(UsageRecord.status == "SUCCEEDED").count() == 3


def run(client, mail, gateway, monkeypatch, make_user):
    email, pid, scene_ids, img, snapshot = full_flow(client, mail, gateway, monkeypatch)
    other_headers, _ = make_user("someone-else@example.com")           # (verification is on in this test, so sign the other user in directly)
    other_headers = auth(other_headers["Authorization"].split()[1])
    with restart_backend() as c2:
        check_after_restart(c2, email, pid, scene_ids, img, snapshot, other_headers)


@pytest.fixture
def make_user(client, mail):
    """A verified second user (verification is switched on by the mail fixture, so go through the real code step)."""
    def _make(email="other@example.com", password=PW):
        register(client, email, password, "Other")
        token = verify(client, email, re.search(r"code is (\d{6})\.", mail.to(email)[-1][2]).group(1)).json()["access_token"]
        return auth(token), None
    return _make


def test_everything_survives_a_backend_restart_with_local_storage(client, mail, gateway, monkeypatch, make_user):
    run(client, mail, gateway, monkeypatch, make_user)


def test_everything_survives_a_backend_restart_with_object_storage(client, mail, gateway, monkeypatch, make_user, s3_app):
    run(client, mail, gateway, monkeypatch, make_user)
    keys = [o["Key"] for o in s3_app.list_objects_v2(Bucket="dc-test-bucket")["Contents"]]
    assert len(keys) == 8 and sum(k.endswith("thumbnail.jpg") for k in keys) == 4           # every asset has its file and thumbnail in the bucket
