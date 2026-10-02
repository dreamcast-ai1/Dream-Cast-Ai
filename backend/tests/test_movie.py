"""Scenes and movie assembly. Clips are tiny real MP4s made with the bundled FFmpeg; the video provider is the usual fake."""
import os
import subprocess
import tempfile

import imageio_ffmpeg
import pytest

from app import media
from app.config import get_settings
from app.db import SessionLocal
from app.models import GeneratedAsset, GenerationJob, Scene, UsageRecord
from app.services import jobs
from app.storage import get_storage

from .helpers import SCRIPT, FakeFal, generate, make_project, run_all, use_fal

pytestmark = pytest.mark.usefixtures("all_features", "advanced_options")

_CLIPS: dict = {}


def clip_bytes(size="320x180", seconds=2, audio=False) -> bytes:
    key = (size, seconds, audio)
    if key not in _CLIPS:
        path = os.path.join(tempfile.mkdtemp(), "c.mp4")
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=10"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
        subprocess.run(cmd + ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-shortest", path], capture_output=True, check=True)
        _CLIPS[key] = open(path, "rb").read()
    return _CLIPS[key]


def new_scene(client, h, pid, **body):
    r = client.post(f"/api/projects/{pid}/scenes", headers=h, json={"title": "A scene", "description": "A knight rides at dawn.", **body})
    assert r.status_code == 201, r.text
    return r.json()


def give_clip(scene_id, data=None, user_id=None):
    """Attach an already 'generated' clip to a scene (what a finished video job does)."""
    with SessionLocal() as db:
        scene = db.get(Scene, scene_id)
        key = get_storage().save("generated", scene.project_id, "video.mp4", data or clip_bytes())
        a = GeneratedAsset(project_id=scene.project_id, user_id=scene.user_id, type="VIDEO", title="clip", file_path=key, format="mp4",
                           mime_type="video/mp4", duration_seconds=2.0, status="READY", provider="fal-video", meta={"scene_id": scene_id})
        db.add(a)
        db.flush()
        a.lineage_id = a.id
        scene.video_asset_id = a.id
        db.commit()
        return a.id


def state(client, h, pid):
    return client.get(f"/api/projects/{pid}/movie", headers=h).json()


def video_used(client, h):
    return next(i["used"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == "video")


def movie_assets(client, h, pid):
    return [a for a in client.get(f"/api/projects/{pid}/assets?type=VIDEO", headers=h).json() if a["meta"].get("movie")]


# ------------------------------------------------------------------ scenes
def test_scene_crud_is_free_and_numbered(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    a, b, c = (new_scene(client, h, pid, title=t) for t in ("One", "Two", "Three"))
    assert [s["number"] for s in (a, b, c)] == [1, 2, 3]
    assert {"id", "number", "title", "description", "script", "characters", "character_ids", "visual_prompt", "duration_seconds", "status", "assets", "video", "job"} <= set(a)
    assert a["status"] == "DRAFT" and a["duration_seconds"] == 10 and a["video"] is None
    r = client.patch(f"/api/projects/{pid}/scenes/{c['id']}", headers=h, json={"number": 1, "title": "Opening", "script": "INT. HALL", "visual_prompt": "A hall"})
    assert r.status_code == 200 and r.json()["number"] == 1 and r.json()["title"] == "Opening"
    nums = {s["title"]: s["number"] for s in client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"]}
    assert nums == {"Opening": 1, "Two": 2, "One": 3}                      # moving onto a number swaps
    assert client.delete(f"/api/projects/{pid}/scenes/{b['id']}", headers=h).status_code == 204
    assert [(s["number"], s["title"]) for s in client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"]] == [(1, "Opening"), (2, "One")]
    assert client.post(f"/api/projects/{pid}/scenes", headers=h, json={"number": 2}).status_code == 409
    assert video_used(client, h) == 0                                      # none of that cost a video credit


def test_scene_duration_follows_the_15_second_rule(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    s = new_scene(client, h, pid, duration_seconds=60)
    assert s["duration_seconds"] == 15 and any("15 seconds" in n for n in s["notes"])
    assert new_scene(client, h, pid, duration_seconds=5)["duration_seconds"] == 5
    assert new_scene(client, h, pid)["duration_seconds"] == 10
    r = client.patch(f"/api/projects/{pid}/scenes/{s['id']}", headers=h, json={"duration_seconds": 120}).json()
    assert r["duration_seconds"] == 15 and r["notes"]


def test_scenes_belong_to_their_owner(client, make_user):
    ha, _ = make_user("a@example.com")
    hb, _ = make_user("b@example.com")
    pid = make_project(client, ha)
    s = new_scene(client, ha, pid)
    assert client.get(f"/api/projects/{pid}/scenes", headers=hb).status_code == 404
    assert client.post(f"/api/projects/{pid}/scenes", headers=hb, json={}).status_code == 404
    assert client.patch(f"/api/projects/{pid}/scenes/{s['id']}", headers=hb, json={"title": "x"}).status_code == 404
    assert client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-video", headers=hb).status_code == 404
    assert client.post(f"/api/projects/{pid}/movie/assemble", headers=hb).status_code == 404
    pid_b = make_project(client, hb)                                         # own project, someone else's scene id
    assert client.patch(f"/api/projects/{pid_b}/scenes/{s['id']}", headers=hb, json={"title": "x"}).status_code == 404
    assert client.get(f"/api/projects/{pid}/scenes", headers={}).status_code == 401


def test_scenes_can_be_created_from_a_script(client, make_user, monkeypatch):
    h, _ = make_user()
    pid = make_project(client, h)
    client.post(f"/api/projects/{pid}/characters", headers=h, json={"name": "Kael"})
    with SessionLocal() as db:
        a = GeneratedAsset(project_id=pid, user_id=client.get("/api/auth/me", headers=h).json()["id"],
                           type="SCRIPT", title="Script", text_content=SCRIPT, status="READY", format="txt")
        db.add(a)
        db.flush()
        a.lineage_id = a.id
        db.commit()
        script_id = a.id
    r = client.post(f"/api/projects/{pid}/scenes/from-script", headers=h, json={"script_asset_id": script_id})
    assert r.status_code == 201, r.text
    items = r.json()["items"]
    assert [s["number"] for s in items] == [1, 2, 3]
    assert items[0]["title"].startswith("EXT. MOUNTAIN PASS") and items[0]["visual_prompt"] and "KAEL" in items[0]["script"]
    assert "Kael" in items[0]["characters"] and all(s["status"] == "DRAFT" for s in items)
    assert client.post(f"/api/projects/{pid}/scenes/from-script", headers=h, json={"script_asset_id": script_id}).status_code == 409
    assert client.post(f"/api/projects/{pid}/scenes/from-script", headers=h, json={"script_asset_id": script_id, "replace": True}).status_code == 201
    assert client.post(f"/api/projects/{pid}/scenes/from-script", headers=h, json={"script_asset_id": "nope"}).status_code == 404
    assert video_used(client, h) == 0


# ------------------------------------------------------------------ scene video generation (existing Phase 4 pipeline)
def test_scene_video_uses_the_normal_video_job_and_allowance(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    s = new_scene(client, h, pid, visual_prompt="A knight rides across a misty field at dawn.", duration_seconds=10)
    r = client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-video", headers=h)
    assert r.status_code == 201, r.text
    jid = r.json()["job_id"]
    mid = client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"][0]
    assert mid["status"] == "GENERATING" and mid["job"]["id"] == jid and video_used(client, h) == 1          # reserved at submit
    assert client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-video", headers=h).status_code == 409   # no double submit
    run_all()
    done = client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"][0]
    assert done["status"] == "READY" and done["video"]["url"] and done["video"]["asset_id"] == done["assets"][0]["id"]
    assert video_used(client, h) == 1                                          # one generation = one credit, not charged again
    job = client.get(f"/api/jobs/{jid}", headers=h).json()
    assert job["status"] == "COMPLETED" and job["options"]["duration_seconds"] == 10 and job["options"]["aspect_ratio"] == "16:9"
    assert state(client, h, pid)["can_assemble"] is True


def test_scene_video_failure_refunds_like_any_video(client, make_user):
    h, _ = make_user()                                                         # no provider key in tests
    pid = make_project(client, h)
    s = new_scene(client, h, pid)
    jid = client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-video", headers=h).json()["job_id"]
    run_all()
    j = client.get(f"/api/jobs/{jid}", headers=h).json()
    assert j["status"] == "FAILED" and "has not been configured" in j["error_message"]
    assert video_used(client, h) == 0
    sc = client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"][0]
    assert sc["status"] == "FAILED" and sc["job"]["error_message"]


def test_scene_video_respects_the_monthly_limit_and_empty_scenes(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    empty = client.post(f"/api/projects/{pid}/scenes", headers=h, json={}).json()
    r = client.post(f"/api/projects/{pid}/scenes/{empty['id']}/generate-video", headers=h)
    assert r.status_code == 422 and video_used(client, h) == 0                 # invalid requests cost nothing
    s = [new_scene(client, h, pid, visual_prompt=f"Shot {i} of a knight") for i in range(6)]
    codes = [client.post(f"/api/projects/{pid}/scenes/{x['id']}/generate-video", headers=h).status_code for x in s]
    assert codes == [201] * 5 + [429]                                       # Teaser allows 5 videos a month
    assert video_used(client, h) == 5


# ------------------------------------------------------------------ assembly
def test_assembly_is_blocked_when_a_scene_is_missing(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    r = client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    assert r.status_code == 422 and "at least one scene" in r.json()["error"]["message"]
    s = [new_scene(client, h, pid, title=f"S{i}") for i in range(3)]
    give_clip(s[0]["id"])
    give_clip(s[2]["id"])
    st = state(client, h, pid)
    assert st["can_assemble"] is False and st["missing"] == ["Scene 2 has not been generated yet."]
    r = client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    assert r.status_code == 422 and r.json()["error"]["message"] == "Scene 2 has not been generated yet." and r.json()["error"]["code"] == "scenes_missing"
    with SessionLocal() as db:
        assert db.query(GenerationJob).filter_by(type="movie").count() == 0    # nothing was queued


def test_assembly_runs_in_the_background_and_joins_scenes_in_order(client, make_user, monkeypatch):
    stages = []
    orig = jobs.set_stage
    monkeypatch.setattr(jobs, "set_stage", lambda db, j, stage, progress=None: (stages.append(stage), orig(db, j, stage, progress))[1])
    h, _ = make_user()
    pid = make_project(client, h)
    ids = [new_scene(client, h, pid, title=f"S{i}")["id"] for i in range(3)]
    for sid in ids:
        give_clip(sid, clip_bytes(seconds=2))
    r = client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "QUEUED"             # returned immediately, nothing has run
    jid = r.json()["job_id"]
    assert state(client, h, pid)["active_job"]["id"] == jid and movie_assets(client, h, pid) == []
    assert client.post(f"/api/projects/{pid}/movie/assemble", headers=h).status_code == 409        # already assembling
    run_all()
    assert [s for s in stages if s != "PREPARING"] == ["ASSEMBLING", "FINALIZING"]
    j = client.get(f"/api/jobs/{jid}", headers=h).json()
    assert j["status"] == "COMPLETED" and j["type"] == "movie" and j["output"]["scene_count"] == 3 and j["provider"] is None
    [m] = movie_assets(client, h, pid)
    assert m["type"] == "VIDEO" and m["format"] == "mp4" and m["mime_type"] == "video/mp4" and m["has_file"] and m["thumbnail_url"]
    assert m["duration_seconds"] == pytest.approx(6.0, abs=0.6) and m["provider"] == "ffmpeg" and m["version"] == 1
    n = client.get("/api/notifications", headers=h).json()["items"][0]
    assert n["title"] == "Your movie is ready." and n["asset_id"] == m["id"]
    st = state(client, h, pid)
    assert st["active_job"] is None and st["movie"]["id"] == m["id"]
    # downloadable and a real decodable file
    d = client.get(f"/api/assets/{m['id']}/download", headers=h)
    assert d.status_code == 200 and d.headers["content-type"] == "video/mp4"
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(d.content)
        f.flush()
        info = media.probe(f.name)
    assert info and (info.width, info.height) == (320, 180) and info.duration == pytest.approx(6.0, abs=0.6)
    assert client.get(m["url"], headers=h).status_code == 200                  # streamable through the existing media route


def test_assembly_does_not_use_video_credits_or_call_a_provider(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    ids = [new_scene(client, h, pid)["id"] for _ in range(2)]
    for sid in ids:
        give_clip(sid)
    before = video_used(client, h)
    client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    run_all()
    assert movie_assets(client, h, pid) and video_used(client, h) == before == 0
    with SessionLocal() as db:
        assert db.query(UsageRecord).count() == 0                              # no usage rows at all
    # even with the allowance fully spent, assembling still works
    for _ in range(3):
        with SessionLocal() as db:
            db.add(UsageRecord(user_id=client.get("/api/auth/me", headers=h).json()["id"], generator_type="video", status="SUCCEEDED"))
            db.commit()
    assert client.post(f"/api/projects/{pid}/movie/assemble", headers=h).status_code == 202


def test_scene_order_follows_scene_numbers_not_creation_order(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    a, b = new_scene(client, h, pid, title="A"), new_scene(client, h, pid, title="B")
    give_clip(a["id"], clip_bytes(seconds=2))
    give_clip(b["id"], clip_bytes(seconds=3))
    client.patch(f"/api/projects/{pid}/scenes/{b['id']}", headers=h, json={"number": 1})
    client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    with SessionLocal() as db:
        job = db.query(GenerationJob).filter_by(type="movie").one()
        assert job.options["scene_ids"] == [b["id"], a["id"]] and job.options["numbers"] == [1, 2]
    run_all()
    [m] = movie_assets(client, h, pid)
    assert m["duration_seconds"] == pytest.approx(5.0, abs=0.7)


def test_different_sizes_and_mixed_audio_still_assemble(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    ids = [new_scene(client, h, pid)["id"] for _ in range(3)]
    give_clip(ids[0], clip_bytes("320x180", 2, audio=True))
    give_clip(ids[1], clip_bytes("160x160", 2, audio=False))
    give_clip(ids[2], clip_bytes("320x180", 2, audio=True))
    jid = client.post(f"/api/projects/{pid}/movie/assemble", headers=h).json()["job_id"]
    run_all()
    j = client.get(f"/api/jobs/{jid}", headers=h).json()
    assert j["status"] == "COMPLETED", j["error_message"]
    assert j["output"]["notes"] == ["Scene 2 was resized to match Scene 1."]
    [m] = movie_assets(client, h, pid)
    assert (m["meta"]["width"], m["meta"]["height"]) == (320, 180) and m["duration_seconds"] == pytest.approx(6.0, abs=0.7)
    data = client.get(f"/api/assets/{m['id']}/download", headers=h).content
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data)
        f.flush()
        assert media.probe(f.name).has_audio


def test_the_assembled_movie_may_be_longer_than_30_seconds(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    ids = [new_scene(client, h, pid, duration_seconds=30)["id"] for _ in range(2)]
    for sid in ids:
        give_clip(sid, clip_bytes(seconds=16))
    client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    run_all()
    [m] = movie_assets(client, h, pid)
    assert m["duration_seconds"] > 30


def test_reassembling_adds_a_new_version_and_keeps_the_old_one(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    sid = new_scene(client, h, pid)["id"]
    give_clip(sid)
    for _ in range(2):
        client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
        run_all()
    versions = sorted(a["version"] for a in movie_assets(client, h, pid))
    assert versions == [1, 2]
    assert state(client, h, pid)["movie"]["version"] == 2


def test_clip_deleted_after_queueing_fails_clearly(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    ids = [new_scene(client, h, pid)["id"] for _ in range(2)]
    assets = [give_clip(i) for i in ids]
    jid = client.post(f"/api/projects/{pid}/movie/assemble", headers=h).json()["job_id"]
    assert client.delete(f"/api/assets/{assets[1]}", headers=h).status_code == 204
    run_all()
    j = client.get(f"/api/jobs/{jid}", headers=h).json()
    assert j["status"] == "FAILED" and j["error_message"] == "Scene 2 has not been generated yet."
    assert state(client, h, pid)["missing"] == ["Scene 2 has not been generated yet."]


def test_unreadable_clip_fails_the_job_without_crashing_the_worker(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    ids = [new_scene(client, h, pid)["id"] for _ in range(2)]
    give_clip(ids[0])
    give_clip(ids[1], b"this is not a video at all")
    jid = client.post(f"/api/projects/{pid}/movie/assemble", headers=h).json()["job_id"]
    run_all()
    j = client.get(f"/api/jobs/{jid}", headers=h).json()
    assert j["status"] == "FAILED" and "Scene 2" in j["error_message"] and movie_assets(client, h, pid) == []
    n = client.get("/api/notifications", headers=h).json()["items"][0]
    assert n["type"] == "job_failed"
    assert client.post(f"/api/projects/{pid}/movie/assemble", headers=h).status_code == 202      # can try again


def test_queued_assembly_can_be_cancelled(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    give_clip(new_scene(client, h, pid)["id"])
    jid = client.post(f"/api/projects/{pid}/movie/assemble", headers=h).json()["job_id"]
    assert client.post(f"/api/jobs/{jid}/cancel", headers=h).json()["status"] == "CANCELLED"
    run_all()
    assert movie_assets(client, h, pid) == [] and state(client, h, pid)["active_job"] is None


def test_assembled_movie_is_private_to_its_owner(client, make_user):
    ha, _ = make_user("a@example.com")
    hb, _ = make_user("b@example.com")
    pid = make_project(client, ha)
    give_clip(new_scene(client, ha, pid)["id"])
    client.post(f"/api/projects/{pid}/movie/assemble", headers=ha)
    run_all()
    [m] = movie_assets(client, ha, pid)
    assert client.get(f"/api/assets/{m['id']}", headers=hb).status_code == 404
    assert client.get(f"/api/assets/{m['id']}/download", headers=hb).status_code == 404
    assert client.get(m["url"], headers=hb).status_code in (401, 403, 404)
    assert client.get(f"/api/projects/{pid}/movie", headers=hb).status_code == 404


def test_worker_restart_requeues_an_interrupted_assembly(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    give_clip(new_scene(client, h, pid)["id"])
    jid = client.post(f"/api/projects/{pid}/movie/assemble", headers=h).json()["job_id"]
    with SessionLocal() as db:
        job = jobs.claim_next(db)
        assert job.id == jid and job.status == "PROCESSING"
        jobs.recover_interrupted(db)                                             # what the worker does at startup
    run_all()
    assert client.get(f"/api/jobs/{jid}", headers=h).json()["status"] == "COMPLETED"
