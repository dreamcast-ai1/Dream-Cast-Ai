import json
import threading
import time

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import GenerationJob
from app.security import create_token
from app.services import jobs, runner
from app.storage import get_storage

from .conftest import PNG
from .helpers import (FakeFal, generate, generate_and_run, make_png, make_project, mp4_bytes, run_all, upload_ref, use_fal, use_llm, used)

PROMPT = "A warrior walks slowly through an ancient city at night."
OPTS = {"style": "Cinematic", "duration_seconds": 10, "aspect_ratio": "16:9"}


def refine(client, h, prompt=PROMPT, options=None, **kw):
    return client.post("/api/generate/refine", headers=h, json={"generator_type": "video", "prompt": prompt, "options": options or {}, **kw})


def video_job(client, h, fake=None, **kw):
    r = generate(client, h, "video", prompt=kw.pop("prompt", PROMPT), options=kw.pop("options", OPTS), **kw)
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def job(client, h, jid):
    return client.get(f"/api/jobs/{jid}", headers=h).json()


def assets(client, h, pid, type_="VIDEO"):
    return client.get(f"/api/projects/{pid}/assets?type={type_}", headers=h).json()


# ------------------------------------------------------------------ configuration & admin
def test_video_schema_and_provider_not_configured(client, make_user):
    h, _ = make_user()
    v = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}["video"]
    f = {x["key"]: x for x in v["fields"]}
    assert f["method"]["choices"] == ["Text to Video", "Image to Video"] and f["method"]["default"] == "Text to Video"
    assert f["style"]["choices"] == ["Cinematic", "Realistic", "Anime", "3D", "Cartoon", "Fantasy", "Horror", "Sci-Fi", "Documentary", "Custom"]
    assert f["duration_seconds"]["default"] == 10 and f["aspect_ratio"]["choices"] == ["16:9", "9:16", "1:1"]
    assert v["ui"] == "video" and v["configured"] is False and v["available"] is True
    assert v["config_message"] == "Video generation is currently unavailable because the video provider has not been configured."


def test_video_schema_follows_provider_limits(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "video_max_seconds", 10)
    monkeypatch.setattr(get_settings(), "video_aspect_ratios", "16:9,9:16")
    h, _ = make_user()
    f = {x["key"]: x for g in client.get("/api/generate/schema", headers=h).json()["generators"] if g["id"] == "video" for x in g["fields"]}
    assert f["duration_seconds"]["choices"] == ["10"] and f["aspect_ratio"]["choices"] == ["16:9", "9:16"]


def test_admin_sees_video_and_face_providers_without_secrets(client, make_user, monkeypatch):
    ah, _ = make_user("boss@example.com")
    listing = lambda: {p["name"]: p for p in client.get("/api/admin/providers", headers=ah).json()["providers"]}
    p = listing()
    assert p["fal-video"]["label"] == "Video" and p["fal-video"]["configured"] is False and p["fal-video"]["info"]["key_configured"] is False
    assert p["fal-face"]["label"] == "Face replacement" and p["fal-face"]["configured"] is False
    use_fal(monkeypatch, FakeFal(), face=True)
    p = listing()
    assert p["fal-video"]["configured"] and p["fal-video"]["info"]["key_configured"] and p["fal-video"]["info"]["model"].endswith("text-to-video")
    assert p["fal-video"]["info"]["image_to_video"] is True and p["fal-face"]["configured"]
    assert "test-video-key" not in json.dumps(p) and "test-face-key" not in json.dumps(p)
    dumped = json.dumps(client.get("/api/generate/schema", headers=ah).json())
    assert "test-video-key" not in dumped


def test_admin_can_disable_the_video_provider(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    client.put("/api/admin/providers/fal-video", json={"enabled": False}, headers=ah)
    r = generate(client, h, "video", prompt=PROMPT, options=OPTS)
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


# ------------------------------------------------------------------ duration and aspect handling
@pytest.mark.parametrize("prompt,options,expected,note", [
    (PROMPT, {}, 10, None),
    (PROMPT + " Make it 20 seconds.", {"duration_seconds": 10}, 20, None),
    (PROMPT + " Make it 45 seconds.", {"duration_seconds": 10}, 30, "Maximum video duration is 30 seconds. Duration adjusted to 30 seconds."),
    (PROMPT + " Make it 60 seconds.", {}, 30, "Maximum video duration is 30 seconds. Duration adjusted to 30 seconds."),
    (PROMPT + " Make it 100 seconds long.", {}, 30, "Maximum video duration is 30 seconds. Duration adjusted to 30 seconds."),
    (PROMPT + " Make the video longer.", {"duration_seconds": 10}, 30, "maximum video duration (30 seconds)"),
    (PROMPT, {"duration_seconds": 20}, 20, None),
    (PROMPT, {"duration_seconds": 45}, 30, "Maximum video duration is 30 seconds."),
])
def test_video_duration_rules(client, make_user, prompt, options, expected, note):
    h, _ = make_user()
    r = refine(client, h, prompt, options).json()
    assert r["metadata"]["options"]["duration_seconds"] == expected
    if note:
        assert any(note in w for w in r["metadata"]["warnings"])
    if expected == 30 and note and "45" in prompt:
        assert r["refined_prompt"].rstrip().endswith("Final duration: 30 seconds.")      # nothing else can be misread downstream


def test_duration_is_capped_again_by_the_provider_limit(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "video_max_seconds", 10)
    h, _ = make_user()
    r = refine(client, h, PROMPT + " Make it 45 seconds.").json()
    assert r["metadata"]["options"]["duration_seconds"] == 10
    warnings = " ".join(r["metadata"]["warnings"])
    assert "Maximum video duration is 30 seconds. Duration adjusted to 30 seconds." in warnings and "supports clips up to 10 seconds" in warnings
    for bad in (20, 30, 45):     # the server never trusts the client: nothing above the provider's limit is accepted at submit
        r = generate(client, h, "video", prompt=PROMPT, options={**OPTS, "duration_seconds": bad})
        assert r.status_code == 422, bad
    assert used(client, h, "video") == 0


def test_aspect_ratio_is_mapped_not_stretched(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "video_aspect_ratios", "16:9,9:16")
    h, _ = make_user()
    r = refine(client, h, options={"aspect_ratio": "1:1"}).json()
    assert r["metadata"]["options"]["aspect_ratio"] in ("16:9", "9:16")
    assert any("doesn't support 1:1" in w and "closest supported" in w for w in r["metadata"]["warnings"])
    bad = generate(client, h, "video", prompt=PROMPT, options={**OPTS, "aspect_ratio": "1:1"})
    assert bad.status_code == 422 and "16:9, 9:16" in bad.json()["error"]["message"]


def test_multiple_versions_request_never_silently_makes_more(client, make_user):
    h, _ = make_user()
    r = refine(client, h, PROMPT + " Give me 4 versions.").json()
    assert any("one video per generation" in w and "3 video generations remaining today" in w for w in r["metadata"]["warnings"])


# ------------------------------------------------------------------ text-to-video, end to end (mocked provider)
def test_text_to_video_success_stores_video_thumbnail_asset_and_notifies(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal())
    stages = []
    orig = jobs.set_stage
    monkeypatch.setattr(jobs, "set_stage", lambda db, j, stage, progress=None: (stages.append(stage), orig(db, j, stage, progress))[1])
    h, _ = make_user()
    pid = make_project(client, h)
    jid = video_job(client, h, project_id=pid)
    assert job(client, h, jid)["status"] == "QUEUED"                           # returned immediately, nothing ran yet
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "COMPLETED" and j["progress"] is None and j["provider"] == "fal-video" and j["simulated"] is False
    assert [s for s in stages if s != "PREPARING"] == ["SUBMITTING", "GENERATING", "DOWNLOADING", "STORING"]
    sent = fake.submits[0]
    assert sent["model"].endswith("text-to-video")
    assert sent["payload"] == {"prompt": PROMPT, "duration": "10", "aspect_ratio": "16:9"}
    assert fake.headers_seen[0]["authorization"] == "Key test-video-key" and fake.status_calls == 3        # polled until COMPLETED
    [a] = assets(client, h, pid)
    assert a["type"] == "VIDEO" and a["status"] == "READY" and a["format"] == "mp4" and a["mime_type"] == "video/mp4"
    assert a["provider"] == "fal-video" and a["project_id"] == pid and a["has_file"] and a["thumbnail_url"]
    assert a["duration_seconds"] == pytest.approx(2.0, abs=0.3) and a["meta"]["width"] == 320 and a["meta"]["height"] == 180
    assert a["meta"]["aspect_ratio"] == "16:9" and a["meta"]["provider_job_id"] == "req1" and a["meta"]["options"]["style"] == "Cinematic"
    assert a["prompt"] == PROMPT and a["meta"]["original_prompt"] == PROMPT and a["meta"]["requested_duration"] == 10
    assert client.get(a["thumbnail_url"], headers=h).content[:3] == b"\xff\xd8\xff"                       # a JPEG thumbnail
    d = client.get(f"/api/assets/{a['id']}", headers=h).json()
    assert d["job_id"] == jid and d["meta"]["thumbnail"]
    n = client.get("/api/notifications", headers=h).json()["items"][0]
    assert n["title"] == "Your video is ready." and n["asset_id"] == a["id"] and n["project_id"] == pid and n["job_id"] == jid
    assert used(client, h, "video") == 1
    assert "test-video-key" not in json.dumps([j, a, d])


def test_video_file_is_streamed_with_range_support_and_signed_urls(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    h2, _ = make_user("b@example.com")
    pid = make_project(client, h)
    generate_and_run(client, h, "video", prompt=PROMPT, options=OPTS, project_id=pid)
    a = assets(client, h, pid)[0]
    r = client.post("/api/media/stream-url", headers=h, json={"kind": "asset", "id": a["id"]})
    assert r.status_code == 200
    url = r.json()["url"]
    assert "test" not in url and url.startswith("/api/media/")
    full = client.get(url)                                                       # no Authorization header: the signed URL is the credential
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4" and full.content == mp4_bytes()
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100 and part.headers["content-range"].startswith("bytes 0-99/")
    dl = client.get(url + "?download=1")
    assert "attachment" in dl.headers["content-disposition"] and dl.content == mp4_bytes()
    # access control
    assert client.post("/api/media/stream-url", headers=h2, json={"kind": "asset", "id": a["id"]}).status_code == 404
    assert client.post("/api/media/stream-url", json={"kind": "asset", "id": a["id"]}).status_code == 401
    assert client.get("/api/media/not-a-token").status_code == 401
    expired = create_token(f"asset:{a['id']}", "media", minutes=-1, extra={"u": "x"})
    assert client.get(f"/api/media/{expired}").status_code == 401
    access_token_as_media = h["Authorization"].split()[1]
    assert client.get(f"/api/media/{access_token_as_media}").status_code == 401     # wrong purpose
    assert client.get(a["url"], headers=h2).status_code == 404 and client.get(a["thumbnail_url"], headers=h2).status_code == 404
    assert client.get(f"/api/assets/{a['id']}/download", headers=h2).status_code == 404
    dlh = client.get(f"/api/assets/{a['id']}/download", headers=h)
    assert dlh.status_code == 200 and dlh.content == mp4_bytes() and dlh.headers["content-disposition"].endswith('-video-v1.mp4"')


def test_video_asset_delete_and_duplicate_handle_file_and_thumbnail(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "video", prompt=PROMPT, options=OPTS, project_id=pid)
    a = assets(client, h, pid)[0]
    size = lambda: get_storage().usage_bytes(f"generated/{pid}")
    before = size()
    c = client.post(f"/api/assets/{a['id']}/duplicate", headers=h).json()
    assert size() == before * 2 and c["thumbnail_url"] and client.get(c["thumbnail_url"], headers=h).status_code == 200
    assert client.delete(f"/api/assets/{c['id']}", headers=h).status_code == 204 and size() == before
    assert client.delete(f"/api/assets/{a['id']}", headers=h).status_code == 204 and size() == 0


def test_regenerate_video_creates_new_version_and_new_job(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "video", prompt=PROMPT, options=OPTS, project_id=pid)
    a = assets(client, h, pid)[0]
    assert client.post(f"/api/assets/{a['id']}/regenerate", headers=h).status_code == 201
    fake.status_calls = 0
    run_all()
    assert sorted(x["version"] for x in assets(client, h, pid)) == [1, 2] and len(fake.submits) == 2


# ------------------------------------------------------------------ image-to-video, references, characters
def test_image_to_video_sends_the_uploaded_image_and_needs_one(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    img = make_png(200, 120, 33)
    ref = upload_ref(client, h, pid, img, "warrior.png", "CHARACTER")
    opts = {**OPTS, "method": "Image to Video"}
    body = "Make the warrior slowly walk forward while smoke moves behind him."
    assert generate(client, h, "video", prompt=body, options=opts, project_id=pid).status_code == 422       # needs a source image
    r = generate(client, h, "video", prompt=body, options=opts, project_id=pid, reference_assets=[ref["id"]])
    assert r.status_code == 201
    run_all()
    j = job(client, h, r.json()["job_id"])
    assert j["status"] == "COMPLETED"
    sent = fake.submits[0]
    assert sent["model"].endswith("image-to-video") and sent["payload"]["prompt"] == body and sent["payload"]["duration"] == "10"
    assert "aspect_ratio" not in sent["payload"]
    import base64
    head, b64 = sent["payload"]["image_url"].split(",", 1)
    assert head == "data:image/png;base64" and base64.b64decode(b64) == img
    a = assets(client, h, pid)[0]
    assert a["meta"]["method"] == "Image to Video" and a["meta"]["source_asset_id"] == ref["id"]


def test_image_to_video_prompt_is_optional(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    ref = upload_ref(client, h, pid, make_png(64, 64, 5))
    body = {"generator_type": "video", "prompt": "", "project_id": pid, "reference_assets": [ref["id"]], "options": {"method": "Image to Video"}}
    assert client.post("/api/generate/refine", headers=h, json=body).status_code == 200
    assert client.post("/api/generate/refine", headers=h, json={**body, "reference_assets": []}).status_code == 422
    assert refine(client, h, "").status_code == 422                          # text-to-video still needs a prompt


def test_provider_without_image_to_video_says_so(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(), video_provider_i2v_model="")
    h, _ = make_user()
    pid = make_project(client, h)
    ref = upload_ref(client, h, pid, make_png(64, 64, 6))
    r = generate(client, h, "video", prompt="x y z", options={**OPTS, "method": "Image to Video"}, project_id=pid, reference_assets=[ref["id"]])
    assert r.status_code == 422 and r.json()["error"]["message"] == "This provider does not support image-to-video."
    assert fake.submits == [] and used(client, h, "video") == 0


def test_reference_and_character_are_described_not_faked_as_consistency(client, make_user, monkeypatch):
    llm = use_llm(monkeypatch)
    fake = use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    ch = client.post(f"/api/projects/{pid}/characters", headers=h, json={"name": "Arjun", "appearance": "tall, scarred", "clothing": "worn leather armor"}).json()
    client.post(f"/api/projects/{pid}/characters", headers=h, json={"name": "Maya", "appearance": "red cloak"})
    ref = upload_ref(client, h, pid, make_png(80, 80, 9), "city.png", "LOCATION")
    r = refine(client, h, "Arjun walks through the ancient city.", project_id=pid,
               options={"character_ids": [ch["id"]]}, reference_assets=[ref["id"]]).json()
    assert r["metadata"]["context_used"]["characters"] == 1 and r["metadata"]["context_used"]["references"] == 1
    sent = llm.bodies()[-1]["messages"][-1]["content"]
    assert "Arjun" in sent and "worn leather armor" in sent and "Maya" not in sent and "city.png" in sent and "(location)" in sent
    assert any("references are described in the prompt" in w for w in r["metadata"]["warnings"])
    generate_and_run(client, h, "video", prompt="Arjun walks.", options={**OPTS, "character_ids": [ch["id"]]}, project_id=pid, reference_assets=[ref["id"]])
    assert "image_url" not in fake.submits[0]["payload"]                     # text-to-video sends no reference images


def test_reference_upload_deduplicates_and_validates(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    a = client.post(f"/api/projects/{pid}/references", headers=h, data={"type": "OBJECT"}, files={"file": ("a.png", make_png(70, 70, 1), "image/png")})
    b = client.post(f"/api/projects/{pid}/references", headers=h, data={"type": "OBJECT"}, files={"file": ("copy.png", make_png(70, 70, 1), "image/png")})
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len(client.get(f"/api/projects/{pid}/references", headers=h).json()) == 1
    assert a.json()["width"] == 70 and a.json()["height"] == 70
    tiny = client.post(f"/api/projects/{pid}/references", headers=h, data={"type": "FACE"}, files={"file": ("f.png", PNG, "image/png")})
    assert tiny.status_code == 422 and tiny.json()["error"]["code"] == "image_too_small"
    exe = client.post(f"/api/projects/{pid}/references", headers=h, data={"type": "FACE"}, files={"file": ("virus.png", b"MZ\x90\x00" + b"\0" * 300, "image/png")})
    assert exe.status_code == 415


# ------------------------------------------------------------------ script scene / story / project style -> video
def _script_and_story(client, h, monkeypatch, pid):
    use_llm(monkeypatch)
    generate_and_run(client, h, "story", project_id=pid)
    generate_and_run(client, h, "script", project_id=pid)
    return (assets(client, h, pid, "STORY")[0]["id"], assets(client, h, pid, "SCRIPT")[0]["id"])


def test_script_scene_supplies_full_scene_context_to_video(client, make_user, monkeypatch):
    h, _ = make_user()
    pid = make_project(client, h)
    client.post(f"/api/projects/{pid}/characters", headers=h, json={"name": "Kael", "appearance": "scarred warrior", "clothing": "dark armor"})
    client.post(f"/api/projects/{pid}/characters", headers=h, json={"name": "Nobody", "appearance": "irrelevant"})
    _, script_id = _script_and_story(client, h, monkeypatch, pid)
    s = client.get(f"/api/assets/{script_id}/scenes/2", headers=h).json()
    assert s["location"] == "INT. SLEEPING CITY" and s["time"] == "NIGHT" and s["camera"].startswith("Wide")
    assert s["dialogue"] == [{"speaker": "Queen", "line": "You are late, warrior."}] and "lanterns flare" in s["action"]
    assert s["characters"] == ["Kael"] and len(s["character_ids"]) == 1 and "SLEEPING CITY" in s["video_prompt"]
    assert client.get(f"/api/assets/{script_id}/scenes/9", headers=h).status_code == 404
    llm = use_llm(monkeypatch)
    opts = {"script_asset_id": script_id, "scene_number": 2, "character_ids": s["character_ids"]}
    r = refine(client, h, s["video_prompt"], opts, project_id=pid).json()
    assert r["metadata"]["context_used"]["scene"] is True and r["metadata"]["context_used"]["characters"] == 1
    sent = llm.bodies()[-1]["messages"][-1]["content"]
    assert "Script scene 2:" in sent and "SLEEPING CITY" in sent and "Wide" in sent and "Dialogue (context only)" in sent and "dark armor" in sent
    assert "MOUNTAIN PASS" not in sent and "TOWER" not in sent and "Nobody" not in sent          # irrelevant scenes/characters stay out
    job_id = video_job(client, h, project_id=pid, options={**OPTS, **opts}, prompt=s["video_prompt"])
    with SessionLocal() as db:
        ctx = db.get(GenerationJob, job_id).context
    assert ctx["scene_fields"]["number"] == 2 and "MOUNTAIN" not in json.dumps(ctx)
    other = make_user("b@example.com")[0]
    assert client.get(f"/api/assets/{script_id}/scenes/2", headers=other).status_code == 404


def test_story_section_becomes_video_context(client, make_user, monkeypatch):
    h, _ = make_user()
    pid = make_project(client, h)
    story_id, _ = _script_and_story(client, h, monkeypatch, pid)
    secs = client.get(f"/api/assets/{story_id}/sections", headers=h).json()["sections"]
    assert [x["key"] for x in secs] == ["LOGLINE", "SETTING", "ACT 1", "ACT 2", "ACT 3", "ENDING"]
    llm = use_llm(monkeypatch)
    r = refine(client, h, "The queen asks for help", {"story_asset_id": story_id, "story_section": "ACT 2"}, project_id=pid).json()
    assert r["metadata"]["context_used"]["story_section"] is True
    sent = llm.bodies()[-1]["messages"][-1]["content"]
    assert "Story section: ACT 2:" in sent and "break a curse" in sent and "faces the cursed guardian" not in sent and "ACT 1" not in sent
    assert refine(client, h, "x y z", {"story_asset_id": story_id, "story_section": "ACT 9"}, project_id=pid).status_code == 422
    assert refine(client, h, "x y z", {"story_asset_id": story_id}, project_id=make_project(client, h, "Other")).status_code == 422


def test_project_style_is_context_but_never_overrides_the_users_choice(client, make_user, monkeypatch):
    llm = use_llm(monkeypatch)
    h, _ = make_user()
    r = client.post("/api/projects", headers=h, json={"title": "Styled", "genre": "Fantasy", "style": "Cinematic realism"})
    assert r.status_code == 201 and r.json()["style"] == "Cinematic realism"
    pid = r.json()["id"]
    assert client.patch(f"/api/projects/{pid}", headers=h, json={"style": "Painterly"}).json()["style"] == "Painterly"
    client.patch(f"/api/projects/{pid}", headers=h, json={"style": "Cinematic realism"})
    refine(client, h, PROMPT, {"style": "Anime"}, project_id=pid)
    sent = llm.bodies()[-1]["messages"][-1]["content"]
    assert "style: Anime" in sent and "Project visual style: Cinematic realism (use it only if the request doesn't choose a style)" in sent
    assert client.get(f"/api/projects/{pid}", headers=h).json()["style"] == "Cinematic realism"


# ------------------------------------------------------------------ failures, retry, polling, cancellation, resume
def test_video_provider_not_configured_fails_cleanly_and_refunds(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    j = generate_and_run(client, h, "video", prompt=PROMPT, options=OPTS, project_id=pid)
    assert j["status"] == "FAILED" and j["error_code"] == "API_NOT_CONFIGURED"
    assert j["error_message"] == "Video generation is currently unavailable because the video provider has not been configured."
    assert assets(client, h, pid) == [] and used(client, h, "video") == 0
    assert client.get("/api/notifications", headers=h).json()["items"][0]["title"] == "Your video generation failed."


def test_provider_outage_is_retried_once_then_reported(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(submit_status=500))
    h, _ = make_user()
    jid = video_job(client, h)
    run_all()
    assert job(client, h, jid)["status"] == "RETRYING"
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and j["attempts"] == 2 and len(fake.submits) == 2
    assert j["error_message"] == "Video generation failed because the configured video provider is unavailable."
    assert used(client, h, "video") == 0                       # the provider never accepted a job


@pytest.mark.parametrize("status,code,attempts,refunded", [(401, "AUTHENTICATION_ERROR", 1, True), (403, "QUOTA_EXCEEDED", 1, True),
                                                             (422, "INVALID_REQUEST", 1, True), (400, "INVALID_REQUEST", 1, True)])
def test_video_provider_error_categories_are_not_retried(client, make_user, monkeypatch, status, code, attempts, refunded):
    use_fal(monkeypatch, FakeFal(submit_status=status))
    h, _ = make_user()
    jid = video_job(client, h)
    run_all()
    j = job(client, h, jid)
    assert (j["status"], j["error_code"], j["attempts"]) == ("FAILED", code, attempts)
    assert "test-video-key" not in json.dumps(j) and "Traceback" not in json.dumps(j)
    assert used(client, h, "video") == 0


def test_job_failure_inside_the_model_after_accepting_keeps_the_allowance_spent(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal(result_status=422))
    h, _ = make_user()
    jid = video_job(client, h)
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and j["error_code"] == "GENERATION_FAILED" and "couldn't complete" in j["error_message"]
    assert used(client, h, "video") == 1 and j["attempts"] == 1        # not retried (would double-charge); manual retry creates a NEW job
    r = client.post(f"/api/jobs/{jid}/regenerate", headers=h)
    assert r.status_code == 201 and r.json()["job_id"] != jid and job(client, h, jid)["status"] == "FAILED"


def test_provider_timeout_stops_the_job_and_tries_to_cancel_remotely(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(statuses=("IN_PROGRESS",)))
    monkeypatch.setattr(get_settings(), "job_timeout_seconds", 0)
    h, _ = make_user()
    jid = video_job(client, h)
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and "took too long" in j["error_message"] and fake.cancel_calls == 1


def test_transient_polling_errors_never_resubmit(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(status_errors=3))
    h, _ = make_user()
    jid = video_job(client, h)
    run_all()
    assert job(client, h, jid)["status"] == "COMPLETED" and len(fake.submits) == 1


def test_persistent_polling_failure_fails_without_double_submit(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(status_errors=100))
    monkeypatch.setattr(get_settings(), "job_max_auto_retries", 0)
    h, _ = make_user()
    jid = video_job(client, h)
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and j["error_code"] == "PROVIDER_UNAVAILABLE" and len(fake.submits) == 1


def test_non_video_output_is_rejected(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal(result_bytes=b"this is not a video at all" * 20), )
    monkeypatch.setattr(get_settings(), "job_max_auto_retries", 0)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "video", prompt=PROMPT, options=OPTS, project_id=pid)
    assert assets(client, h, pid) == []                            # no asset for a file that isn't real video
    assert client.get("/api/jobs", headers=h).json()[0]["status"] in ("FAILED", "RETRYING")


def test_oversized_download_is_refused(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal(), video_max_download_mb=0)
    monkeypatch.setattr(get_settings(), "job_max_auto_retries", 0)
    h, _ = make_user()
    jid = video_job(client, h)
    run_all()
    assert job(client, h, jid)["error_code"] == "STORAGE_ERROR"


def _cancel_running(client, h, fake):
    jid = video_job(client, h)
    with SessionLocal() as db:
        claimed = jobs.claim_next(db)
    t = threading.Thread(target=runner.run_job, args=(claimed.id,))
    t.start()
    for _ in range(60):
        time.sleep(0.05)
        if job(client, h, jid)["stage"] == "GENERATING":
            break
    assert client.post(f"/api/jobs/{jid}/cancel", headers=h).status_code == 200
    t.join(timeout=10)
    assert not t.is_alive()
    return job(client, h, jid)


def test_cancel_uses_provider_cancellation_when_it_confirms(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(statuses=("IN_QUEUE",), cancel_status=202))
    h, _ = make_user()
    j = _cancel_running(client, h, fake)
    assert j["status"] == "CANCELLED" and j["output"]["cancellation"] == "provider_cancelled" and fake.cancel_calls == 1
    assert used(client, h, "video") == 1                       # the provider had accepted the job


def test_cancel_is_honest_when_the_provider_refuses(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(statuses=("IN_PROGRESS",), cancel_status=400))
    h, _ = make_user()
    j = _cancel_running(client, h, fake)
    assert j["status"] == "CANCELLED" and j["output"]["cancellation"] == "stopped_locally"


def test_worker_restart_resumes_polling_instead_of_resubmitting(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(statuses=("IN_PROGRESS", "COMPLETED")))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = video_job(client, h, project_id=pid)
    ext = json.dumps({"r": "req1", "s": f"{FakeFal.ORIGIN}/fal-ai/app/requests/req1/status", "u": f"{FakeFal.ORIGIN}/fal-ai/app/requests/req1",
                      "c": f"{FakeFal.ORIGIN}/fal-ai/app/requests/req1/cancel"})
    with SessionLocal() as db:                                    # what a crash mid-generation leaves behind
        row = db.get(GenerationJob, jid)
        row.status, row.stage, row.external_id, row.reached_provider, row.attempts = "PROCESSING", "GENERATING", ext, True, 1
        db.commit()
        assert jobs.recover_interrupted(db) == 1
    run_all()
    assert job(client, h, jid)["status"] == "COMPLETED" and fake.submits == []          # never submitted (or paid for) twice
    assert len(assets(client, h, pid)) == 1


def test_video_usage_limit_and_invalid_input_do_not_consume_allowance(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    assert generate(client, h, "video", prompt="", options=OPTS).status_code == 422
    assert generate(client, h, "video", prompt=PROMPT, options={**OPTS, "aspect_ratio": "4:3"}).status_code == 422
    assert used(client, h, "video") == 0
    for _ in range(3):
        assert generate(client, h, "video", prompt=PROMPT, options=OPTS).status_code == 201
    r = generate(client, h, "video", prompt=PROMPT, options=OPTS)
    assert r.status_code == 429 and r.json()["error"]["code"] == "QUOTA_EXCEEDED" and "Video limit" in r.json()["error"]["message"]
    assert used(client, h, "video") == 3


def test_generation_history_links_video_thumbnail(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "video", prompt=PROMPT, options=OPTS, project_id=pid)
    j = client.get("/api/jobs?type=video", headers=h).json()[0]
    assert j["assets"][0]["thumbnail_url"] and client.get(j["assets"][0]["thumbnail_url"], headers=h).status_code == 200
