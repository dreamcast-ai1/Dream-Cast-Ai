import httpx
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import UsageRecord
from app.providers import registry
from app.providers import text as text_module

from .conftest import PNG
from .helpers import make_png

pytestmark = pytest.mark.usefixtures("all_features")


def project(client, h, title="The Lost Kingdom"):
    return client.post("/api/projects", json={"title": title, "genre": "Fantasy", "description": "A forgotten realm."}, headers=h).json()["id"]


def refine(client, h, **kw):
    body = {"generator_type": "video", "prompt": "A cinematic warrior walking through an ancient city at night.", "options": {}, **kw}
    return client.post("/api/generate/refine", json=body, headers=h)


def generate(client, h, **kw):
    """Submits a generation. Defaults to the avatar generator, which runs on the (fake, labelled) dev simulator, so generic
    job-pipeline tests never depend on a real provider. Provider-specific tests pass generator_type explicitly."""
    body = {"generator_type": "ai_avatar", "original_prompt": "A warrior walks.", "refined_prompt": "A warrior walks through a city.",
            "options": {"style": "Cinematic", "duration_seconds": 10, "aspect_ratio": "16:9"}, **kw}
    return client.post("/api/generations", json=body, headers=h)


# ---------- authentication protection
def test_generation_endpoints_require_auth(client):
    assert client.get("/api/generate/schema").status_code == 401
    assert client.post("/api/generate/refine", json={"generator_type": "video", "prompt": "x"}).status_code == 401
    assert client.post("/api/generations", json={"generator_type": "video"}).status_code == 401
    assert client.get("/api/jobs/abc").status_code == 401


def test_schema_describes_all_generators(client, make_user):
    h, _ = make_user()
    gens = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}
    assert set(gens) == {"video", "image", "music", "voice", "lyrics", "story", "script", "face_replacement", "ai_avatar", "interactive_avatar"}
    assert [f["key"] for f in gens["video"]["fields"]] == ["method", "style", "duration_seconds", "aspect_ratio"]
    assert [f["key"] for f in gens["lyrics"]["fields"]] == ["language"]      # lyrics: prompt + optional language only
    assert gens["face_replacement"]["reference"] == "required"
    assert gens["video"]["available"] and gens["video"]["simulated"] is False and gens["video"]["configured"] is False
    assert gens["video"]["config_message"] == "Video generation is currently unavailable because the video provider has not been configured."
    assert gens["ai_avatar"]["simulated"] is True


# ---------- prompt refinement
def test_refine_valid_uses_template_without_llm_key(client, make_user):
    h, _ = make_user()
    r = refine(client, h, options={"style": "Cinematic", "duration_seconds": 10, "aspect_ratio": "16:9"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metadata"]["method"] == "template" and body["metadata"]["warnings"]
    assert "ancient city" in body["refined_prompt"] and "16:9" in body["refined_prompt"]
    assert body["structured_prompt"]["generator"] == "video"


def test_refine_missing_prompt_and_unsupported_generator(client, make_user):
    h, _ = make_user()
    assert refine(client, h, prompt="").status_code == 422
    assert refine(client, h, prompt="  ").status_code == 422
    assert refine(client, h, generator_type="hologram").status_code == 400
    assert refine(client, h, prompt="x" * 4001).status_code == 422
    assert refine(client, h, options={"style": "Bogus"}).status_code == 422


def test_refine_music_prompt_optional_but_needs_something(client, make_user):
    h, _ = make_user()
    assert refine(client, h, generator_type="music", prompt="", options={"genre": "Lo-fi"}).status_code == 200
    assert refine(client, h, generator_type="music", prompt="", options={}).status_code == 422
    assert refine(client, h, generator_type="lyrics", prompt="a song about rain").status_code == 200


def test_video_duration_is_capped_at_30_not_silently_forwarded(client, make_user):
    h, _ = make_user()
    r = refine(client, h, prompt="A knight rides a horse. Make it 45 seconds.").json()
    assert r["metadata"]["options"]["duration_seconds"] == 30
    assert "Maximum video duration is 30 seconds. Duration adjusted to 30 seconds." in r["metadata"]["warnings"]
    r = refine(client, h, prompt="A knight rides.", options={"duration_seconds": 45}).json()
    assert r["metadata"]["options"]["duration_seconds"] == 30
    r = refine(client, h, prompt="A knight rides for 2 minutes").json()
    assert r["metadata"]["options"]["duration_seconds"] == 30
    r = refine(client, h, prompt="A knight rides for 20 seconds").json()
    assert r["metadata"]["options"]["duration_seconds"] == 20
    assert refine(client, h, prompt="A knight rides").json()["metadata"]["options"]["duration_seconds"] == 10
    # The Create form always sends its default (10). A duration typed in the prompt must still win / be capped.
    r = refine(client, h, prompt="A dragon circles a castle. Make it 45 seconds.", options={"duration_seconds": 10}).json()
    assert r["metadata"]["options"]["duration_seconds"] == 30
    assert "Maximum video duration is 30 seconds. Duration adjusted to 30 seconds." in r["metadata"]["warnings"]
    assert r["refined_prompt"].rstrip().endswith("Final duration: 30 seconds.")
    r = refine(client, h, prompt="A dragon, 20 seconds", options={"duration_seconds": 10}).json()
    assert r["metadata"]["options"]["duration_seconds"] == 20 and any("20-second" in w for w in r["metadata"]["warnings"])
    r = refine(client, h, prompt="A knight rides", options={"duration_seconds": 15}).json()
    assert r["metadata"]["options"]["duration_seconds"] in (10, 20) and r["metadata"]["warnings"]


def test_refine_project_ownership_and_context(client, make_user):
    h1, _ = make_user("a@example.com")
    h2, _ = make_user("b@example.com")
    pid = project(client, h1)
    client.post(f"/api/projects/{pid}/characters", json={"name": "Aria", "appearance": "silver hair"}, headers=h1)
    assert refine(client, h2, project_id=pid).status_code == 404
    r = refine(client, h1, generator_type="story", project_id=pid).json()
    assert "Aria" in r["refined_prompt"] and "The Lost Kingdom" in r["refined_prompt"]
    assert r["metadata"]["context_used"]["characters"] == 1
    # video only includes characters the user actually picked
    assert refine(client, h1, project_id=pid).json()["metadata"]["context_used"]["characters"] == 0
    cid = client.get(f"/api/projects/{pid}/characters", headers=h1).json()[0]["id"]
    picked = refine(client, h1, project_id=pid, options={"character_ids": [cid]}).json()
    assert picked["metadata"]["context_used"]["characters"] == 1 and "Aria" in picked["refined_prompt"]
    assert refine(client, h1, project_id=pid, options={"character_ids": []}).json()["metadata"]["context_used"]["characters"] == 0


def test_context_is_generator_specific(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    client.post(f"/api/projects/{pid}/characters", json={"name": "Aria", "appearance": "silver hair", "personality": "bold"}, headers=h)
    music = refine(client, h, generator_type="music", prompt="battle theme", project_id=pid).json()
    assert music["metadata"]["context_used"]["characters"] == 0 and "Aria" not in music["refined_prompt"]
    script = refine(client, h, generator_type="script", prompt="opening scene", project_id=pid).json()
    assert script["metadata"]["context_used"]["characters"] == 1


def _mock_llm(monkeypatch, handler):
    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key-not-real")
    monkeypatch.setattr(text_module, "http_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(handler)))


def test_refine_calls_configured_llm_once_and_hides_key(client, make_user, monkeypatch):
    h, _ = make_user()
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "  LLM REFINED: warrior, ancient city, night, 10 seconds  "}}]})

    _mock_llm(monkeypatch, handler)
    r = refine(client, h, options={"style": "Cinematic", "duration_seconds": 10, "aspect_ratio": "16:9"})
    body = r.json()
    assert len(calls) == 1 and calls[0].headers["authorization"] == "Bearer test-key-not-real"
    sent = calls[0].content.decode()
    assert "ancient city" in sent and "16:9" in sent and "preserve the user's intent" in sent
    assert body["refined_prompt"] == "LLM REFINED: warrior, ancient city, night, 10 seconds"
    assert body["metadata"]["method"] == "llm" and body["metadata"]["provider"] == "groq"
    assert "test-key-not-real" not in r.text
    with SessionLocal() as db:
        assert db.query(UsageRecord).filter_by(generator_type="refinement").count() == 1


@pytest.mark.parametrize("status,needle", [(500, "unavailable"), (429, "unavailable"), (401, "rejected its API key")])
def test_refine_falls_back_to_template_when_llm_fails(client, make_user, monkeypatch, status, needle):
    h, _ = make_user()
    _mock_llm(monkeypatch, lambda req: httpx.Response(status, json={"error": "nope"}))
    body = refine(client, h).json()
    assert body["metadata"]["method"] == "template"
    assert any(needle in w for w in body["metadata"]["warnings"])
    with SessionLocal() as db:
        assert db.query(UsageRecord).filter_by(generator_type="refinement").count() == 0


def test_refine_daily_cap_stops_llm_calls(client, make_user, monkeypatch):
    h, _ = make_user()
    calls = []
    _mock_llm(monkeypatch, lambda req: (calls.append(1), httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))[1])
    monkeypatch.setattr(get_settings(), "refine_daily_limit", 2)
    for _ in range(4):
        refine(client, h)
    assert len(calls) == 2


# ---------- submitting generations
def test_generate_returns_immediately_with_queued_job(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    r = generate(client, h, generator_type="video", project_id=pid, options={"style": "Cinematic", "duration_seconds": 10, "aspect_ratio": "16:9"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "QUEUED" and body["job_id"]
    job = client.get(f"/api/jobs/{body['job_id']}", headers=h).json()
    assert job["status"] == "QUEUED" and job["stage"] == "QUEUED" and job["progress"] is None
    assert job["project_id"] == pid and job["project_title"] == "The Lost Kingdom"
    assert job["refined_prompt"] == "A warrior walks through a city." and job["options"]["duration_seconds"] == 10
    assert [j["id"] for j in client.get("/api/jobs", headers=h).json()] == [body["job_id"]]
    used = {i["generator"]: i["used"] for i in client.get("/api/usage", headers=h).json()["items"]}
    assert used["video"] == 1


def test_generate_validation(client, make_user):
    h, _ = make_user()
    v = dict(generator_type="video")
    assert generate(client, h, generator_type="hologram").status_code == 400
    assert generate(client, h, refined_prompt="", **v).status_code == 422
    assert generate(client, h, original_prompt="", **v).status_code == 422
    r = generate(client, h, options={"duration_seconds": 45}, **v)
    assert r.status_code == 422 and "30 seconds" in r.json()["error"]["message"]
    assert generate(client, h, options={"aspect_ratio": "4:3"}, **v).status_code == 422
    assert generate(client, h, refined_prompt="x" * 8001, **v).status_code == 422
    with SessionLocal() as db:
        assert db.query(UsageRecord).count() == 0      # rejected requests never consume quota


def test_generate_unauthorized_project_and_parent(client, make_user):
    h1, _ = make_user("a@example.com")
    h2, _ = make_user("b@example.com")
    pid = project(client, h1)
    assert generate(client, h2, project_id=pid).status_code == 404
    parent = generate(client, h1).json()["job_id"]
    assert generate(client, h2, parent_id=parent).status_code == 404


def test_generate_accepts_spec_style_type_names(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    src = client.post(f"/api/projects/{pid}/references", headers=h, data={"type": "SOURCE"}, files={"file": ("s.png", make_png(shade=10), "image/png")}).json()
    face = client.post(f"/api/projects/{pid}/references", headers=h, data={"type": "FACE"}, files={"file": ("f.png", make_png(shade=200), "image/png")}).json()
    r = client.post("/api/generations", headers=h, json={
        "generator_type": "FACE", "original_prompt": "", "refined_prompt": "Swap face", "project_id": pid, "reference_assets": [src["id"], face["id"]],
        "options": {"source_asset_id": src["id"], "face_asset_id": face["id"], "permission_confirmed": True}})
    assert r.status_code == 201, r.text
    r = client.post("/api/generations", headers=h, json={"generator_type": "INTERACTIVE_AVATAR", "original_prompt": "museum guide",
                                                            "refined_prompt": "A museum guide"})
    assert r.status_code == 201
    jobs = {j["type"] for j in client.get("/api/jobs?type=FACE,AVATAR,interactive_avatar", headers=h).json()}
    assert jobs == {"face_replacement", "interactive_avatar"}


def test_face_and_reference_rules(client, make_user):
    h, _ = make_user()
    pid = project(client, h)
    face = dict(generator_type="face_replacement", original_prompt="", refined_prompt="swap")
    assert client.post("/api/generations", json=face, headers=h).status_code == 422          # face image required
    assert client.post("/api/generations", json={**face, "project_id": pid}, headers=h).status_code == 422
    assert client.post("/api/generations", json={**face, "project_id": pid, "reference_assets": ["nope"]}, headers=h).status_code == 422
    # reference belonging to another project/user is rejected
    h2, _ = make_user("b@example.com")
    pid2 = project(client, h2)
    ref = client.post(f"/api/projects/{pid2}/references", headers=h2, data={"type": "OTHER"}, files={"file": ("f.png", PNG, "image/png")}).json()
    assert client.post("/api/generations", json={**face, "project_id": pid, "reference_assets": [ref["id"]]}, headers=h).status_code == 422
    # generators that take no references reject them
    assert generate(client, h, generator_type="lyrics", options={}, project_id=pid, reference_assets=[ref["id"]]).status_code == 422


def test_usage_limit_enforced_per_generator(client, make_user):
    h, _ = make_user()
    for _ in range(3):
        assert generate(client, h).status_code == 201
    r = generate(client, h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert generate(client, h, generator_type="music", original_prompt="epic", refined_prompt="epic music", options={}).status_code == 201
    h2, _ = make_user("b@example.com")
    assert generate(client, h2).status_code == 201       # limits are per user


def test_interactive_avatar_allows_twelve_per_day(client, make_user):
    h, _ = make_user()
    body = dict(generator_type="interactive_avatar", original_prompt="chat", refined_prompt="chat")
    assert [client.post("/api/generations", json=body, headers=h).status_code for _ in range(13)] == [201] * 12 + [429]


def test_no_provider_configured_blocks_submission_without_using_quota(client, make_user, monkeypatch):
    h, _ = make_user()
    saved = dict(registry._providers)
    registry._providers.pop("dev-simulator", None)
    try:
        r = generate(client, h)
        assert r.status_code == 503 and r.json()["error"]["code"] == "API_NOT_CONFIGURED"
        gens = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}
        assert gens["ai_avatar"]["available"] is False and gens["ai_avatar"]["unavailable_reason"]
        assert client.get("/api/jobs", headers=h).json() == []
        assert client.get("/api/usage", headers=h).json()["items"][0]["used"] == 0
    finally:
        registry._providers.update(saved)
