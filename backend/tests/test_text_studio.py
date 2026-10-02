"""Story and Story-to-Script generation (POST /api/text/story, /api/text/script) with Gemini as the text model. Every provider call is a mocked HTTP
response: no real Gemini request is ever made. Gemini is selected exactly as in production: LLM_PROVIDER=gemini + LLM_API_KEY (+ optional LLM_MODEL)."""
import json
import logging

import httpx
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import GeneratedAsset, GenerationJob, UsageRecord
from app.providers import text as text_module
from app.providers.text import PromptRefinementProvider
from app.services import text_studio

from .helpers import STORY, _client, make_project

KEY = "AIza-test-gemini-key-SECRETVALUE"

STORY_DATA = {
    "title": "The Lantern Keeper",
    "logline": "A lighthouse keeper's daughter must relight the last lantern before a storm closes the harbour.",
    "setting": "A small fishing harbour and its old lighthouse, one stormy autumn night.",
    "characters": [{"name": "Mara", "description": "A sixteen-year-old who knows every stair of the lighthouse."},
                   {"name": "Tomas", "description": "Her father, a keeper away at sea."}],
    "beginning": "Mara lives in the shadow of the old lighthouse while her father fishes far out at sea.",
    "middle": "The storm bells ring and the lantern goes dark as the boats turn for home.",
    "climax": "Mara climbs the stairs alone with the last of the oil and lights the lantern with shaking hands.",
    "ending": "By dawn every boat is safe, and the harbour has a new keeper.",
    "full_story": "Mara had lived all sixteen years of her life in the shadow of the old lighthouse.\n\nWhen the storm bells rang, her father was far out at sea and the lantern had gone dark.\n\nShe climbed the stairs alone, lit the lantern, and by dawn every boat was safe.",
}
SCRIPT_DATA = {
    "title": "The Lantern Keeper",
    "logline": "A keeper's daughter relights the lantern before a storm.",
    "characters": [{"name": "Mara", "description": "sixteen, brave, knows the lighthouse"}, {"name": "Old Pedro", "description": "harbourmaster"}],
    "scenes": [
        {"number": 1, "heading": "EXT. HARBOUR - DUSK", "location": "Harbour", "time": "Dusk", "action": "Fishing boats rock at their moorings as dark clouds gather over the old lighthouse.",
         "narration": "Long ago, a single lantern kept this harbour alive.", "dialogue": [], "sound": "Wind, creaking ropes", "camera": "Wide establishing shot", "transition": "cut to", "estimated_seconds": 20},
        {"number": 2, "heading": "INT. LIGHTHOUSE STAIRCASE - NIGHT", "location": "Lighthouse staircase", "time": "Night", "action": "Mara races up the spiral stairs, an oil can swinging from her hand.",
         "narration": "", "dialogue": [{"speaker": "mara", "line": "Hold on, Father. Just hold on."}, {"speaker": "Old Pedro", "line": "The boats are turning home, girl!"}],
         "sound": "Thunder, echoing footsteps", "camera": "", "transition": "CUT TO", "estimated_seconds": 35},
        {"number": 3, "location": "Lantern room", "time": "Dawn", "action": "Mara lights the great lantern and its beam sweeps across the calm morning sea, guiding every boat home.",
         "narration": "By dawn, the harbour had a new keeper.", "dialogue": [], "sound": "", "camera": "", "transition": "FADE OUT"},
    ],
}
STORY_JSON, SCRIPT_JSON = json.dumps(STORY_DATA), json.dumps(SCRIPT_DATA)


def chat(content, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]})


class Mock:
    """A scripted fake of the chat-completions endpoint that remembers every request it receives."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        r = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    @property
    def body(self):
        return json.loads(self.requests[-1].content)


@pytest.fixture
def gemini(monkeypatch):
    s = get_settings()
    for k, v in dict(llm_provider="gemini", llm_api_key=KEY, llm_model="", llm_base_url="", llm_reasoning_effort="").items():
        monkeypatch.setattr(s, k, v)

    def install(*replies) -> Mock:
        mock = Mock(*(replies or [chat(STORY_JSON)]))
        monkeypatch.setattr(text_module, "http_client", _client(mock))
        return mock
    return install


def story(client, h, **kw):
    return client.post("/api/text/story", headers=h, json={"prompt": "A lighthouse keeper's daughter relights the lantern", **kw})


def script(client, h, **kw):
    return client.post("/api/text/script", headers=h, json={"story": STORY, **kw})


pytestmark = pytest.mark.usefixtures("all_features")


def used(client, h, gen):
    return next(i["used"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == gen)


# ------------------------------------------------------------------ Gemini configured: Prompt -> Story
def test_story_comes_back_structured_from_gemini(client, make_user, gemini):
    gemini(chat(STORY_JSON))
    h, _ = make_user()
    r = story(client, h, genre="Drama", tone="Inspirational", length="Short", language="English")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "story" and d["title"] == "The Lantern Keeper" and d["logline"].startswith("A lighthouse keeper's daughter")
    assert d["setting"].startswith("A small fishing harbour") and [c["name"] for c in d["characters"]] == ["Mara", "Tomas"] and d["characters"][0]["description"]
    assert d["beginning"].startswith("Mara lives") and d["middle"].startswith("The storm bells") and d["climax"].startswith("Mara climbs") and d["ending"].startswith("By dawn")
    assert d["full_story"].startswith("Mara had lived") and d["full_story"].count("\n\n") == 2 and d["word_count"] > 30
    assert d["language"] == "English" and d["genre"] == "Drama" and d["tone"] == "Inspirational" and d["model"] == "gemini-3.5-flash-lite" and d["saved"] is None and d["remaining"] == 19
    assert set(d) == {"kind", "title", "logline", "setting", "characters", "beginning", "middle", "climax", "ending", "full_story", "text", "word_count", "language",
                      "genre", "tone", "saved", "model", "remaining"}
    assert d["text"].startswith("TITLE: The Lantern Keeper\nLOGLINE:") and "ACT 1: Mara lives" in d["text"] and "FULL STORY\nMara had lived" in d["text"]       # editable plain text
    assert "choices" not in r.text and "finish_reason" not in r.text                     # never the provider's raw response


def test_the_request_to_gemini_uses_the_openai_compatible_endpoint_json_mode_and_the_story_instructions(client, make_user, gemini):
    mock = gemini(chat(STORY_JSON))
    h, _ = make_user()
    story(client, h, genre="Fantasy", tone="Dark", length="Long", language="Hindi")
    req = mock.requests[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    b = mock.body
    system = b["messages"][0]["content"]
    assert b["model"] == "gemini-3.5-flash-lite" and b["messages"][0]["role"] == "system" and b["messages"][1] == {"role": "user", "content": "A lighthouse keeper's daughter relights the lantern"}
    assert b["response_format"] == {"type": "json_object"} and b["reasoning_effort"] == "low" and b["max_tokens"] > 1024 and b["temperature"] == 0.8
    assert "Genre: Fantasy" in system and "Tone: Dark" in system and "about 1400 words" in system and "Hindi" in system and "COMPLETE short story" in system
    for key in ('"title"', '"logline"', '"setting"', '"characters"', '"beginning"', '"middle"', '"climax"', '"ending"', '"full_story"'):
        assert key in system


def test_llm_model_overrides_the_gemini_default(client, make_user, gemini, monkeypatch):
    mock = gemini(chat(STORY_JSON))
    monkeypatch.setattr(get_settings(), "llm_model", "gemini-3.6-flash")
    h, _ = make_user()
    assert story(client, h).json()["model"] == "gemini-3.6-flash" and mock.body["model"] == "gemini-3.6-flash"


def test_other_providers_use_the_same_openai_compatible_call(client, make_user, gemini, monkeypatch):
    mock = gemini(chat(STORY_JSON))
    monkeypatch.setattr(get_settings(), "llm_provider", "groq")
    h, _ = make_user()
    assert story(client, h, length="Short").status_code == 200
    assert "reasoning_effort" not in mock.body and mock.body["response_format"] == {"type": "json_object"} and str(mock.requests[0].url).startswith("https://api.groq.com/openai/v1/")


def test_a_model_that_rejects_json_mode_or_reasoning_effort_is_retried_without_them(client, make_user, gemini):
    mock = gemini(httpx.Response(400, json={"error": {"message": "unsupported parameter"}}), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200
    second = json.loads(mock.requests[1].content)
    assert len(mock.requests) == 2 and "reasoning_effort" not in second and "response_format" not in second


@pytest.mark.parametrize("wrap", [lambda j: f"```json\n{j}\n```", lambda j: f"Here is your story:\n{j}\nEnjoy!", lambda j: f"  \n{j}\n  "])
def test_json_wrapped_in_a_code_fence_or_a_sentence_is_still_understood(client, make_user, gemini, wrap):
    gemini(chat(wrap(STORY_JSON)))
    h, _ = make_user()
    assert story(client, h).json()["title"] == "The Lantern Keeper"


def test_a_malformed_answer_gets_one_more_try_inside_the_same_allowance_unit(client, make_user, gemini):
    mock = gemini(chat("Sure! Here's a story about a lighthouse..."), chat(STORY_JSON))
    h, _ = make_user()
    r = story(client, h)
    assert r.status_code == 200 and len(mock.requests) == 2 and used(client, h, "story") == 1


@pytest.mark.parametrize("bad", ["not json at all", "[1, 2, 3]", "{}", '{"title": "Only a title"}', json.dumps({**STORY_DATA, "full_story": "too short"}),
                                 json.dumps({**STORY_DATA, "title": ""}), '{"title": "x", "full_story": ', "ok"])
def test_an_invalid_provider_response_is_a_clear_error_and_costs_nothing(client, make_user, gemini, bad):
    mock = gemini(chat(bad))
    h, _ = make_user()
    r = story(client, h)
    assert r.status_code == 502 and r.json()["error"]["code"] == "text_provider_bad_response" and "expected format" in r.json()["error"]["message"]
    assert len(mock.requests) == 2 and used(client, h, "story") == 0 and KEY not in r.text


def test_story_input_is_validated(client, make_user, gemini):
    mock = gemini()
    h, _ = make_user()
    bad = [{"prompt": ""}, {"prompt": "   "}, {"prompt": "hi"}, {"prompt": "x" * 2001}, {"length": "Epic"}, {"language": "Klingon"}, {"genre": "g" * 61}, {"tone": "t" * 61}]
    for body in bad:
        r = client.post("/api/text/story", headers=h, json={"prompt": "A lighthouse keeper's daughter", **body} if "prompt" not in body else body)
        assert r.status_code == 422, body
    assert client.post("/api/text/story", headers=h, json={}).status_code == 422
    assert mock.requests == [] and used(client, h, "story") == 0                       # invalid input never reaches the provider or costs allowance


# ------------------------------------------------------------------ Gemini not configured / provider failures
def test_missing_credentials_give_a_clear_error_and_leak_nothing(client, make_user, gemini, monkeypatch):
    mock = gemini()
    monkeypatch.setattr(get_settings(), "llm_api_key", "")
    h, _ = make_user()
    r = story(client, h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "text_provider_not_configured"
    assert "not configured" in r.json()["error"]["message"] and "LLM_API_KEY" in r.json()["error"]["message"] and KEY not in r.text
    assert mock.requests == [] and used(client, h, "story") == 0
    assert script(client, h).status_code == 503
    assert client.get("/api/text/options", headers=h).json()["configured"] is False


@pytest.mark.parametrize("reply,status,code,fragment", [
    (httpx.ReadTimeout("timed out"), 504, "text_provider_timeout", "took too long"),
    (httpx.ConnectError("refused"), 503, "text_provider_unreachable", "could not be reached"),
    (httpx.Response(429, json={"error": {"message": "quota"}}), 429, "text_provider_rate_limited", "rate-limiting"),
    (httpx.Response(401, json={"error": {"message": "API key not valid"}}), 502, "text_provider_auth", "rejected its API key"),
    (httpx.Response(403, json={"error": {"message": "forbidden"}}), 502, "text_provider_auth", "rejected its API key"),
    (httpx.Response(500, text="RAWBODY boom"), 503, "text_provider_unavailable", "overloaded or unavailable"),
    (httpx.Response(503, text="RAWBODY overloaded"), 503, "text_provider_unavailable", "overloaded or unavailable"),
    (httpx.Response(400, json={"error": {"message": "blocked"}}), 502, "text_provider_rejected", "couldn't process"),
    (httpx.Response(200, text="<html>not json</html>"), 502, "text_provider_bad_response", "unusable answer"),
    (httpx.Response(200, json={"unexpected": True}), 502, "text_provider_bad_response", "unusable answer"),
    (chat(""), 502, "text_provider_bad_response", "unusable answer"),
])
def test_provider_failures_map_to_safe_messages_and_refund(client, make_user, gemini, caplog, reply, status, code, fragment):
    caplog.set_level(logging.DEBUG)
    gemini(reply)
    h, _ = make_user()
    for call in (story, script):
        r = call(client, h)
        assert r.status_code == status and r.json()["error"]["code"] == code and fragment in r.json()["error"]["message"], r.text
        assert KEY not in r.text and "Traceback" not in r.text and "API key not valid" not in r.text and "RAWBODY" not in r.text
    assert used(client, h, "story") == 0 and used(client, h, "script") == 0            # failures are refunded
    assert KEY not in "\n".join(rec.getMessage() for rec in caplog.records if not rec.name.startswith(("httpx", "asyncio")))


def test_a_cut_off_answer_is_logged_and_then_judged_on_its_content(client, make_user, gemini, caplog):
    caplog.set_level(logging.WARNING)
    gemini(chat(STORY_JSON[:200], finish="length"), chat(STORY_JSON[:200], finish="length"))
    h, _ = make_user()
    assert story(client, h).status_code == 502 and "cut off" in caplog.text and used(client, h, "story") == 0


# ------------------------------------------------------------------ Story -> Script
def test_script_is_generated_from_the_story_and_returned_in_a_clean_structure(client, make_user, gemini):
    mock = gemini(chat(SCRIPT_JSON))
    h, _ = make_user()
    r = script(client, h, style="Cinematic", tone="Dramatic", script_format="Narrated video (voice-over)", duration_minutes=10, language="English")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "script" and d["title"] == "The Lantern Keeper" and d["scene_count"] == 3 == len(d["scenes"]) and d["remaining"] == 9
    assert d["characters"] == [{"name": "Mara", "description": "sixteen, brave, knows the lighthouse"}, {"name": "Old Pedro", "description": "harbourmaster"}]
    assert d["style"] == "Cinematic" and d["script_format"] == "Narrated video (voice-over)" and d["duration_minutes"] == 10 and d["tone"] == "Dramatic"
    s1, s2, s3 = d["scenes"]
    assert (s1["number"], s1["heading"], s1["location"], s1["time"]) == (1, "EXT. HARBOUR - DUSK", "Harbour", "Dusk") and "Fishing boats" in s1["action"]
    assert s1["narration"].startswith("Long ago") and s1["transition"] == "CUT TO" and s1["camera"] == "Wide establishing shot" and s1["sound"] == "Wind, creaking ropes"
    assert s2["dialogue"] == [{"speaker": "Mara", "line": "Hold on, Father. Just hold on."}, {"speaker": "Old Pedro", "line": "The boats are turning home, girl!"}]
    assert s3["heading"] == "INT. LANTERN ROOM - DAWN" and s3["number"] == 3                      # a missing slug line is built from location/time
    assert [s1["estimated_seconds"], s2["estimated_seconds"]] == [20, 35] and s3["estimated_seconds"] >= 5 and d["estimated_total_seconds"] == 55 + s3["estimated_seconds"]
    assert set(s1) == {"number", "heading", "location", "time", "action", "narration", "dialogue", "sound", "camera", "transition", "estimated_seconds"}
    assert d["text"].startswith("TITLE:") and "SCENE 01\nEXT. HARBOUR - DUSK" in d["text"] and "Duration: about 20 seconds" in d["text"] and "choices" not in r.text and KEY not in r.text
    system, user = mock.body["messages"][0]["content"], mock.body["messages"][1]["content"]
    assert mock.body["response_format"] == {"type": "json_object"} and "STORY TO ADAPT:" in user and STORY.splitlines()[1] in user
    assert "Do not add, remove or change major" in system and "Script style: Cinematic." in system and "Tone: Dramatic." in system and "about 10 minutes" in system
    assert "voice-over narration" in system and '"estimated_seconds"' in system and '"narration"' in system and '"dialogue"' in system and '"transition"' in system


def test_script_defaults_never_ask_for_plot_changes(client, make_user, gemini):
    mock = gemini(chat(SCRIPT_JSON))
    h, _ = make_user()
    assert script(client, h).status_code == 200
    system, user = mock.body["messages"][0]["content"], mock.body["messages"][1]["content"]
    assert user.startswith("Convert this story into a screenplay.") and "Script style:" not in system and "keep the same characters, events, order of events and ending" in system
    assert "Standard screenplay" in system


def test_extra_instructions_reach_the_prompt(client, make_user, gemini):
    mock = gemini(chat(SCRIPT_JSON))
    h, _ = make_user()
    script(client, h, instructions="Keep it to three scenes and change the ending to a sad one.")
    assert mock.body["messages"][1]["content"].startswith("Keep it to three scenes and change the ending")


def test_missing_scene_details_are_tolerated_and_durations_are_estimated(client, make_user, gemini):
    data = {"title": "Tiny", "scenes": [{"heading": "kitchen - morning", "action": "A cat knocks a cup off the table and watches it fall to the floor slowly.", "estimated_seconds": "soon"},
                                        {"action": "", "dialogue": [], "narration": ""}, "not a scene",
                                        {"action": "The cat leaves.", "dialogue": [{"speaker": "", "line": "ignored"}, {"speaker": "Cat", "line": "Meow."}], "estimated_seconds": 4000}]}
    gemini(chat(json.dumps(data)))
    h, _ = make_user()
    d = script(client, h).json()
    assert d["scene_count"] == 2 and [s["number"] for s in d["scenes"]] == [1, 2] and d["scenes"][0]["heading"] == "INT. KITCHEN - MORNING"
    assert d["scenes"][1]["dialogue"] == [{"speaker": "Cat", "line": "Meow."}] and all(5 <= s["estimated_seconds"] < 900 for s in d["scenes"])        # bad numbers become estimates
    assert d["characters"] == []


@pytest.mark.parametrize("bad", ["not json", "{}", '{"title": "No scenes"}', '{"title": "Empty", "scenes": []}', '{"scenes": [{"action": "something happens here for a while"}]}',
                                 '{"title": "x", "scenes": "nope"}'])
def test_an_invalid_script_response_is_a_clear_error_and_costs_nothing(client, make_user, gemini, bad):
    mock = gemini(chat(bad))
    h, _ = make_user()
    r = script(client, h)
    assert r.status_code == 502 and r.json()["error"]["code"] == "text_provider_bad_response" and len(mock.requests) == 2 and used(client, h, "script") == 0


def test_script_input_is_validated(client, make_user, gemini):
    mock = gemini()
    h, _ = make_user()
    for body in ({"story": ""}, {"story": "   "}, {"story": "too short"}, {"story": "x" * 20001}, {"duration_minutes": 7}, {"duration_minutes": 0}, {"duration_minutes": "ten"},
                 {"language": "Elvish"}, {"style": "s" * 81}, {"tone": "t" * 61}, {"instructions": "i" * 1001}, {"script_format": "Haiku"}):
        assert client.post("/api/text/script", headers=h, json={"story": STORY, **body}).status_code == 422, body
    assert client.post("/api/text/script", headers=h, json={}).status_code == 422
    assert mock.requests == [] and used(client, h, "script") == 0


# ------------------------------------------------------------------ access, limits, secrecy
def test_every_text_endpoint_requires_a_signed_in_user(client, gemini):
    mock = gemini()
    assert client.get("/api/text/options").status_code == 401
    assert client.post("/api/text/story", json={"prompt": "A lighthouse keeper's daughter"}).status_code == 401
    assert client.post("/api/text/script", json={"story": STORY}).status_code == 401
    assert client.post("/api/text/story", headers={"Authorization": "Bearer garbage"}, json={"prompt": "A lighthouse keeper's daughter"}).status_code == 401
    assert mock.requests == []


def test_options_endpoint_lists_choices_and_never_the_key(client, make_user, gemini):
    h, _ = make_user()
    r = client.get("/api/text/options", headers=h)
    d = r.json()
    assert d["configured"] is True and "Fantasy" in d["genres"] and "Custom" not in d["genres"] and d["languages"] == ["English", "Hindi", "Telugu"]
    assert d["durations"] == [5, 10, 20, 30] and d["lengths"] == ["Short", "Medium", "Long"] and d["limits"]["story"] == 20000 and d["tones"] and d["script_styles"]
    assert d["script_formats"] == ["Screenplay", "Narrated video (voice-over)", "Dialogue-led", "Visual (minimal dialogue)"] and KEY not in r.text


def test_api_keys_never_appear_in_any_response(client, make_user, gemini):
    gemini(chat(STORY_JSON), chat(SCRIPT_JSON), httpx.Response(401, text="bad key"))
    h, _ = make_user()
    texts = [story(client, h).text, script(client, h).text, story(client, h).text, client.get("/api/text/options", headers=h).text,
             client.get("/api/admin/providers", headers=h).text, client.get("/api/settings/providers", headers=h).text, client.get("/api/features").text]
    assert KEY not in "\n".join(texts)


def test_the_same_monthly_allowance_as_the_queued_generators_applies(client, make_user, gemini):
    gemini(chat(STORY_JSON))
    h, _ = make_user()
    assert [story(client, h).status_code for _ in range(21)] == [200] * 20 + [429] and used(client, h, "story") == 20               # Teaser: 20 stories a month
    r = story(client, h)
    assert r.json()["error"]["code"] == "QUOTA_EXCEEDED" and "Story limit" in r.json()["error"]["message"]
    gemini(chat(SCRIPT_JSON))
    assert [script(client, h).status_code for _ in range(11)] == [200] * 10 + [429]   # Teaser: 10 scripts a month


def test_a_queued_story_and_a_studio_story_share_one_allowance(client, make_user, gemini):
    from .helpers import generate
    gemini(chat(STORY_JSON))
    h, _ = make_user()
    for _ in range(19):
        assert generate(client, h, "story", prompt="A young engineer finds a hidden city.", project_id=make_project(client, h)).status_code == 201
    assert story(client, h).status_code == 200 and story(client, h).status_code == 429


def test_requests_are_rate_limited_per_ip(client, make_user, gemini, monkeypatch):
    from app.routers import text as text_router
    gemini(chat(STORY_JSON))
    monkeypatch.setattr(get_settings(), "text_rate_limit_per_minute", 2)
    text_router.text_rate_limit.reset()
    h, _ = make_user()
    try:
        assert [story(client, h).status_code for _ in range(3)] == [200, 200, 429]
    finally:
        text_router.text_rate_limit.reset()


# ------------------------------------------------------------------ saving into a project (existing assets, Library and History)
def test_a_story_can_be_saved_into_a_project_as_a_normal_asset(client, make_user, gemini):
    gemini(chat(STORY_JSON))
    h, _ = make_user()
    pid = make_project(client, h)
    d = story(client, h, project_id=pid, genre="Drama").json()
    assert d["saved"]["project_id"] == pid and d["saved"]["asset_id"] and d["saved"]["job_id"]
    [a] = client.get(f"/api/projects/{pid}/assets?type=STORY", headers=h).json()
    assert a["id"] == d["saved"]["asset_id"] and a["title"] == "The Lantern Keeper" and a["status"] == "READY" and a["language"] == "English"
    detail = client.get(f"/api/assets/{a['id']}", headers=h).json()
    assert detail["text_content"] == d["text"] and detail["meta"]["model"] == "gemini-3.5-flash-lite"
    assert [i["id"] for i in client.get("/api/assets?type=STORY", headers=h).json()] == [a["id"]]            # shows up in the Library
    [job] = client.get("/api/jobs", headers=h).json()
    assert job["type"] == "story" and job["status"] == "COMPLETED" and job["assets"][0]["id"] == a["id"]     # and in History
    with SessionLocal() as db:
        rec = db.query(UsageRecord).filter_by(job_id=d["saved"]["job_id"]).one()
        assert rec.status == "SUCCEEDED" and rec.generator_type == "story"
    assert used(client, h, "story") == 1


def test_a_saved_story_can_feed_the_existing_script_generator_context(client, make_user, gemini):
    """The saved story text keeps the ACT labels the rest of the app reads, so story sections still work for video context."""
    from app.textparse import parse_story_sections
    gemini(chat(STORY_JSON))
    h, _ = make_user()
    pid = make_project(client, h)
    asset = client.get(f"/api/assets/{story(client, h, project_id=pid).json()['saved']['asset_id']}", headers=h).json()
    sections = {s["key"]: s["text"] for s in parse_story_sections(asset["text_content"])}
    assert sections["ACT 1"].startswith("Mara lives") and sections["ACT 3"].startswith("Mara climbs") and sections["ENDING"].startswith("By dawn")
    assert client.get(f"/api/assets/{asset['id']}/sections", headers=h).status_code == 200


def test_a_saved_script_keeps_its_scenes_and_can_become_movie_scenes(client, make_user, gemini):
    gemini(chat(SCRIPT_JSON))
    h, _ = make_user()
    pid = make_project(client, h)
    d = script(client, h, project_id=pid).json()
    asset = client.get(f"/api/assets/{d['saved']['asset_id']}", headers=h).json()
    assert [s["number"] for s in asset["meta"]["scenes"]] == [1, 2, 3] and asset["type"] == "SCRIPT" and asset["text_content"] == d["text"]
    r = client.post(f"/api/projects/{pid}/scenes/from-script", headers=h, json={"script_asset_id": asset["id"]})
    assert r.status_code == 201 and len(r.json()["items"]) == 3                                              # Story -> Script -> Scenes works end to end
    first = r.json()["items"][0]
    assert first["title"].startswith("EXT. HARBOUR - DUSK") and "Fishing boats" in first["visual_prompt"]
    detail = client.get(f"/api/assets/{asset['id']}/scenes/2", headers=h).json()
    assert detail["location"] == "INT. LIGHTHOUSE STAIRCASE" and detail["time"] == "NIGHT" and detail["dialogue"][0]["speaker"] == "Mara" and "Duration" not in detail["action"]


def test_saving_into_someone_elses_project_is_refused_before_any_provider_call(client, make_user, gemini):
    mock = gemini(chat(STORY_JSON))
    ha, _ = make_user("a@example.com")
    hb, _ = make_user("b@example.com")
    pid = make_project(client, ha)
    r = story(client, hb, project_id=pid)
    assert r.status_code == 404 and used(client, hb, "story") == 0 and mock.requests == []
    assert script(client, hb, project_id=pid).status_code == 404
    assert client.get("/api/assets", headers=hb).json() == [] and client.get("/api/assets", headers=ha).json() == []
    assert story(client, hb, project_id="does-not-exist").status_code == 404


def test_a_failed_generation_saves_nothing(client, make_user, gemini):
    gemini(httpx.Response(500, text="boom"))
    h, _ = make_user()
    pid = make_project(client, h)
    assert story(client, h, project_id=pid).status_code == 503
    with SessionLocal() as db:
        assert db.query(GeneratedAsset).count() == 0 and db.query(GenerationJob).count() == 0


# ------------------------------------------------------------------ scope: core features stay, optional ones stay hidden, other generators untouched
def test_story_and_script_are_core_features_not_subject_to_feature_hiding(client, gemini, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_api_key", "")                                  # even with no provider configured
    f = client.get("/api/features").json()
    assert f["generators"]["story"] is True and f["generators"]["script"] is True and "story" not in f["hidden"] and "script" not in f["hidden"]
    assert f["asset_types"]["STORY"] is True and f["asset_types"]["SCRIPT"] is True


def test_gemini_is_only_the_text_provider(client, gemini):
    from app.providers import registry
    gemini()
    for p in registry.all():
        if p.name.startswith(("fal-", "huggingface", "google")):
            assert not any(g in p.generators for g in ("story", "script", "lyrics")), p.name       # media providers are untouched
    names = {g: [p.name for p in registry.for_generator(g)] for g in ("image", "video", "music", "voice")}
    assert names == {"image": ["fal-image", "pollinations-image"], "video": ["fal-video", "pollinations-video"], "music": ["huggingface"], "voice": ["google", "knowlez-voice"]}


def test_nothing_is_sent_to_a_media_provider(client, make_user, gemini, monkeypatch):
    """Generating a story/script never triggers an image/video/music/voice request or job."""
    gemini(chat(STORY_JSON), chat(SCRIPT_JSON))
    h, _ = make_user()
    pid = make_project(client, h)
    story(client, h, project_id=pid)
    script(client, h, project_id=pid)
    with SessionLocal() as db:
        assert sorted(j.type for j in db.query(GenerationJob).all()) == ["script", "story"]
        assert {a.type for a in db.query(GeneratedAsset).all()} == {"STORY", "SCRIPT"}
    assert all(i["used"] == 0 for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] not in ("story", "script"))


def test_prompt_refinement_still_works_through_complete(client, make_user, gemini):
    mock = gemini(chat("A refined, detailed prompt about a lighthouse at dusk."))
    h, _ = make_user()
    r = client.post("/api/generate/refine", headers=h, json={"generator_type": "story", "prompt": "a lighthouse story", "options": {}})
    assert r.status_code == 200 and r.json()["metadata"]["method"] == "llm"
    assert mock.body["max_tokens"] > 0 and mock.body["messages"][0]["role"] == "system" and "response_format" not in mock.body       # refinement is plain text


def test_generate_text_is_a_thin_wrapper_over_complete(monkeypatch):
    calls = []
    monkeypatch.setattr(PromptRefinementProvider, "complete",
                        lambda self, system, user, max_tokens=400, temperature=0.4, timeout=None, json_mode=False: calls.append((system, user, max_tokens, temperature, timeout, json_mode)) or "x")
    assert PromptRefinementProvider().generate_text("sys", "usr", max_tokens=900, temperature=0.5, timeout=33.0, json_mode=True) == "x"
    assert PromptRefinementProvider().generate_text("sys", "usr") == "x"
    assert calls == [("sys", "usr", 900, 0.5, 33.0, True), ("sys", "usr", 1500, 0.8, None, False)]


def test_parse_json_object_helper():
    assert text_studio.parse_json_object('{"a": 1}') == {"a": 1} and text_studio.parse_json_object('```\n{"a": 2}\n```') == {"a": 2}
    for bad in ("", "no braces", "[1]", "{broken"):
        with pytest.raises(ValueError):
            text_studio.parse_json_object(bad)
