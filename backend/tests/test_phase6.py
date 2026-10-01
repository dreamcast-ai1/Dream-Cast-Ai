"""Final hardening: production safety defaults, storage resilience, error hygiene and repository hygiene."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import storage as storage_pkg
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.main import app
from app.models import GeneratedAsset, Scene
from app.providers import registry
from app.storage import get_storage

from .helpers import make_project
from .test_movie import clip_bytes, give_clip, movie_assets, new_scene, state

REPO = Path(__file__).resolve().parents[2]


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


# ------------------------------------------------------------------ production defaults
def test_dev_simulator_can_never_run_in_production(monkeypatch):
    monkeypatch.delenv("ENABLE_DEV_SIMULATOR")
    s = Settings(_env_file=None, app_env="production", auth_secret_key="s" * 40, enable_dev_simulator=True)
    assert s.enable_dev_simulator is False
    assert Settings(_env_file=None, app_env="development", enable_dev_simulator=True).enable_dev_simulator is True
    assert Settings(_env_file=None).enable_dev_simulator is False                       # off unless someone turns it on


def test_example_env_has_no_secret_values_and_simulator_off():
    values = dict(line.split("=", 1) for line in (REPO / ".env.example").read_text().splitlines() if "=" in line and not line.startswith("#"))
    for key in ("AUTH_SECRET_KEY", "LLM_API_KEY", "MUSIC_API_KEY", "VOICE_API_KEY", "VIDEO_PROVIDER_API_KEY", "FACE_PROVIDER_API_KEY",
                "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "AUTH_PUBLIC_KEY",
                "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "BREVO_API_KEY"):
        assert values.get(key, "") == "" or values[key].startswith("REPLACE_WITH"), key        # placeholders only
    assert values["ENABLE_DEV_SIMULATOR"] == "false"


# ------------------------------------------------------------------ storage
def test_startup_creates_the_storage_folders(tmp_path, monkeypatch):
    fresh = tmp_path / "wiped-disk" / "storage"
    monkeypatch.setattr(get_settings(), "storage_dir", str(fresh))
    get_storage.cache_clear()
    try:
        with TestClient(app):                                    # runs the startup (lifespan) code
            assert {p.name for p in fresh.iterdir()} >= {"generated", "uploads", "projects"}
    finally:
        monkeypatch.undo()
        get_storage.cache_clear()


def test_files_wiped_from_disk_do_not_crash_anything(client, make_user):
    """Render's disk can be emptied by a redeploy while the database (or a copy of it) still points at the files."""
    h, _ = make_user()
    pid = make_project(client, h)
    ids = [new_scene(client, h, pid)["id"] for _ in range(2)]
    assets = [give_clip(i) for i in ids]
    client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    from .helpers import run_all
    run_all()
    [movie] = movie_assets(client, h, pid)
    root = Path(get_storage().root)
    for child in root.iterdir():
        shutil.rmtree(child, ignore_errors=True)                       # the disk is wiped
    st = state(client, h, pid)                                          # scenes notice their clips are gone, with a clear message
    assert st["can_assemble"] is False and st["missing"] == ["Scene 1 has not been generated yet.", "Scene 2 has not been generated yet."]
    assert [s["status"] for s in client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"]] == ["DRAFT", "DRAFT"]
    assert client.post(f"/api/projects/{pid}/movie/assemble", headers=h).status_code == 422
    for r in (client.get(f"/api/assets/{movie['id']}/download", headers=h), client.get(f"/api/assets/{assets[0]}/download", headers=h)):
        assert r.status_code == 404 and r.json()["error"]["message"] and "Traceback" not in r.text
    assert client.get(f"/api/projects/{pid}", headers=h).status_code == 200      # the project itself is fine
    assert client.get(f"/api/projects/{pid}/assets", headers=h).status_code == 200
    new_scene(client, h, pid)                                           # and the app keeps working: folders are recreated on demand
    sid = ids[0]
    give_clip(sid)
    assert get_storage().exists(client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"][0]["video"] and
                                _asset_key(client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"][0]["video"]["asset_id"]))


def _asset_key(asset_id):
    with SessionLocal() as db:
        return db.get(GeneratedAsset, asset_id).file_path


# ------------------------------------------------------------------ error hygiene
def test_unexpected_errors_never_leak_tracebacks(client, make_user, monkeypatch):
    from app.services import projects as projects_service

    def boom(*a, **k):
        raise RuntimeError("secret internal detail /Users/someone/file.py")
    h, _ = make_user()
    make_project(client, h)
    monkeypatch.setattr(projects_service, "project_out", boom)
    r = client.get("/api/projects", headers=h)
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text and "Traceback" not in r.text and "/Users/" not in r.text


def test_user_facing_messages_for_common_failures(client, make_user):
    h, _ = make_user()
    assert client.post("/api/auth/login", json={"email": "user@example.com", "password": "nope-nope-1"}).json()["error"]["message"] == "Incorrect email or password."
    assert client.get("/api/projects").json()["error"]["code"] == "unauthorized"
    assert client.get("/api/admin/stats", headers=h).json()["error"]["code"] == "forbidden"
    r = client.post("/api/generations", headers=h, json={"generator_type": "video", "original_prompt": "x", "refined_prompt": "x", "options": {"duration_seconds": 45}})
    assert r.status_code == 422 and "30 seconds" in r.json()["error"]["message"]
    r = client.post("/api/subscription/checkout", headers=h, json={"plan_id": "indie"})          # no gateway keys here
    assert r.status_code == 503 and "aren't available" in r.json()["error"]["message"]
    r = client.post("/api/subscription/verify", headers=h, json={"razorpay_order_id": "o", "razorpay_payment_id": "p", "razorpay_signature": "s"})
    assert r.status_code == 404 and r.json()["error"]["message"]


# ------------------------------------------------------------------ repository hygiene (skipped outside a git checkout)
@pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(), reason="needs a git checkout")
def test_gitignore_keeps_media_and_secrets_out_but_the_storage_package_in():
    ignored = lambda p: git("check-ignore", "-q", p).returncode == 0
    for p in (".env", ".env.local", "backend/dreamcast.db", "storage/generated/x/video.mp4", "storage/uploads/a.png", "frontend/node_modules/x"):
        assert ignored(p), p
    for p in ("storage/.gitkeep", "backend/app/storage/local.py", ".env.example", "frontend/public/_redirects"):
        assert not ignored(p), p
    assert git("ls-files", "backend/app/storage").stdout.split() == ["backend/app/storage/__init__.py", "backend/app/storage/base.py", "backend/app/storage/local.py"]
    assert git("ls-files", ".env").stdout.strip() == ""
    assert (REPO / "frontend/public/_redirects").read_text().split() == ["/*", "/index.html", "200"]
    tracked = git("ls-files").stdout.split()
    assert not [t for t in tracked if t.endswith((".db", ".sqlite", ".mp4", ".mp3", ".wav", ".zip")) or t.startswith("storage/generated")]


def test_frontend_api_url_points_at_the_render_backend_and_holds_no_secrets():
    env = (REPO / "frontend/.env.example").read_text()
    assert "VITE_API_URL=https://dreamcast-ai-backend.onrender.com" in env
    for f in (REPO / "frontend/src").rglob("*.ts*"):
        text = f.read_text()
        assert "KEY_SECRET" not in text and "WEBHOOK_SECRET" not in text and "AUTH_SECRET" not in text, f.name
