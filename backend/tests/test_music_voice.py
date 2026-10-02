import pytest
import base64
import json

import httpx

from app.providers.voice import GoogleTTSProvider

from .helpers import (MP3, WAV, generate, generate_and_run, make_project, run_all, use_llm, use_music, use_voice, used)

pytestmark = pytest.mark.usefixtures("all_features", "advanced_options")


def refine(client, h, gen, prompt="", **kw):
    return client.post("/api/generate/refine", headers=h, json={"generator_type": gen, "prompt": prompt, **kw})


def assets(client, h, pid, type_):
    return client.get(f"/api/projects/{pid}/assets?type={type_}", headers=h).json()


def schema(client, h):
    return {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}


MUSIC_PROMPT = "Create a dark cinematic battle theme with deep drums, rising strings and an intense final section."


# ------------------------------------------------------------------ MUSIC
def test_music_configuration_options(client, make_user):
    h, _ = make_user()
    m = schema(client, h)["music"]
    f = {x["key"]: x for x in m["fields"]}
    assert f["genre"]["choices"] == ["Cinematic", "Pop", "Rock", "Classical", "Electronic", "Ambient", "Folk", "Lo-fi", "Horror", "Fantasy", "Custom"]
    assert f["mood"]["choices"] == ["Happy", "Sad", "Epic", "Romantic", "Suspense", "Peaceful", "Dark", "Energetic", "Emotional"]
    assert f["duration_seconds"]["choices"] == ["5", "10", "15"]
    assert m["configured"] is False and "Music provider is not configured" in m["config_message"] and m["available"]


def test_music_duration_follows_the_provider_and_is_never_exceeded(client, make_user, monkeypatch):
    rec = use_music(monkeypatch, music_max_seconds=10)
    h, _ = make_user()
    f = {x["key"]: x for x in schema(client, h)["music"]["fields"]}
    assert f["duration_seconds"]["choices"] == ["5", "10"]
    r = refine(client, h, "music", MUSIC_PROMPT, options={"duration_seconds": 15}).json()
    assert r["metadata"]["options"]["duration_seconds"] == 10
    assert any("up to 10 seconds" in w for w in r["metadata"]["warnings"])
    bad = generate(client, h, "music", MUSIC_PROMPT, options={"duration_seconds": 15})
    assert bad.status_code == 422 and "maximum 10" in bad.json()["error"]["message"]
    assert used(client, h, "music") == 0 and rec.requests == []


def test_music_requires_prompt_or_genre_or_mood(client, make_user):
    h, _ = make_user()
    assert refine(client, h, "music", "", options={}).status_code == 422
    assert refine(client, h, "music", "", options={"mood": "Epic"}).status_code == 200


def test_music_success_with_mocked_provider(client, make_user, monkeypatch):
    llm = use_llm(monkeypatch)
    music = use_music(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    body = {"generator_type": "music", "prompt": MUSIC_PROMPT, "project_id": pid,
            "options": {"genre": "Cinematic", "mood": "Dark", "duration_seconds": 10}}
    ref = client.post("/api/generate/refine", headers=h, json=body).json()
    assert ref["metadata"]["method"] == "llm" and len(llm.requests) == 1
    system = llm.bodies()[0]["messages"][0]["content"]
    assert "Instrumental only" in system and "under 60 words" in system
    job = generate_and_run(client, h, "music", prompt=MUSIC_PROMPT, options=ref["metadata"]["options"], project_id=pid, refined=ref["refined_prompt"])
    assert job["status"] == "COMPLETED" and job["stage"] == "COMPLETED"
    sent = music.bodies()[0]
    assert sent["inputs"] == ref["refined_prompt"] and sent["parameters"]["max_new_tokens"] == 500        # 10 s * 50 tokens/s
    assert music.requests[0].headers["authorization"] == "Bearer test-music-key" and "test-music-key" not in str(music.requests[0].url)
    assert str(music.requests[0].url).endswith("/facebook/musicgen-small")
    [a] = assets(client, h, pid, "MUSIC")
    assert a["has_file"] and a["format"] == "wav" and a["mime_type"] == "audio/wav" and a["duration_seconds"] == 10
    assert a["provider"] == "huggingface" and a["prompt"] == ref["refined_prompt"] and a["project_id"] == pid
    assert a["meta"]["options"]["genre"] == "Cinematic" and a["meta"]["options"]["mood"] == "Dark" and a["meta"]["original_prompt"] == MUSIC_PROMPT
    assert client.get(a["url"], headers=h).content == WAV
    dl = client.get(f"/api/assets/{a['id']}/download", headers=h)
    assert dl.content == WAV and dl.headers["content-type"] == "audio/wav" and "attachment" in dl.headers["content-disposition"]
    assert dl.headers["content-disposition"].endswith('-music-v1.wav"')
    assert used(client, h, "music") == 1 and client.get("/api/notifications", headers=h).json()["items"][0]["type"] == "job_completed"
    assert "test-music-key" not in json.dumps(client.get(f"/api/assets/{a['id']}", headers=h).json())


def test_music_provider_not_configured_is_a_clear_failure_not_a_fake_result(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    job = generate_and_run(client, h, "music", prompt=MUSIC_PROMPT, options={"duration_seconds": 10}, project_id=pid)
    assert job["status"] == "FAILED" and job["error_code"] == "API_NOT_CONFIGURED" and "Music provider is not configured" in job["error_message"]
    assert assets(client, h, pid, "MUSIC") == [] and used(client, h, "music") == 0


def test_music_provider_unavailable_retries_once_then_says_why(client, make_user, monkeypatch):
    use_music(monkeypatch, handler=lambda req: httpx.Response(503, json={"error": "loading"}))
    h, _ = make_user()
    r = generate(client, h, "music", MUSIC_PROMPT, options={"duration_seconds": 10})
    run_all()
    assert client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()["status"] == "RETRYING"
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert j["status"] == "FAILED" and j["attempts"] == 2
    assert j["error_message"] == "Music generation failed because the configured music provider is unavailable."


def test_music_provider_error_mapping(client, make_user, monkeypatch):
    h, _ = make_user()
    for status, code in [(401, "AUTHENTICATION_ERROR"), (402, "QUOTA_EXCEEDED"), (400, "INVALID_REQUEST"), (404, "PROVIDER_UNAVAILABLE")]:
        use_music(monkeypatch, handler=lambda req, s=status: httpx.Response(s, json={"error": "x"}))
        j = generate_and_run(client, h, "music", prompt=MUSIC_PROMPT, options={"duration_seconds": 10})
        assert (j["status"], j["error_code"], j["attempts"]) == ("FAILED", code, 1), status
    use_music(monkeypatch, handler=lambda req: httpx.Response(200, content=b"not audio", headers={"content-type": "application/json"}))
    j = generate_and_run(client, h, "music", prompt="x y z", options={"duration_seconds": 10})
    assert j["status"] in ("RETRYING", "FAILED")
    assert j["error_code"] == "GENERATION_FAILED"


def test_music_uses_lyrics_and_scene_context_but_not_more(client, make_user, monkeypatch):
    llm = use_llm(monkeypatch)
    use_music(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "lyrics", project_id=pid)
    generate_and_run(client, h, "script", project_id=pid)
    lyr = assets(client, h, pid, "LYRICS")[0]["id"]
    scr = assets(client, h, pid, "SCRIPT")[0]["id"]
    n = len(llm.requests)
    r = refine(client, h, "music", "battle theme", project_id=pid, options={"lyrics_asset_id": lyr, "script_asset_id": scr, "scene_number": 2}).json()
    ctx = r["metadata"]["context_used"]
    assert ctx["lyrics"] and ctx["scene"] and len(llm.requests) == n + 1                    # still one refinement call
    sent = llm.bodies()[-1]["messages"][-1]["content"]
    assert "Rise with the morning" in sent and "Scene 2:" in sent and "SLEEPING CITY" in sent
    assert "Scene 1:" not in sent and "Scene 3:" not in sent                                # only the requested scene
    assert refine(client, h, "music", "x", options={"script_asset_id": scr}, project_id=pid).status_code == 200
    assert refine(client, h, "music", "x", options={"scene_number": 2}, project_id=pid).status_code == 422   # a scene needs a script


# ------------------------------------------------------------------ VOICE
def test_voice_configuration_options(client, make_user):
    h, _ = make_user()
    v = schema(client, h)["voice"]
    f = {x["key"]: x for x in v["fields"]}
    assert f["gender"]["choices"] == ["Male", "Female"]
    assert f["accent"]["choices"] == ["Indian English", "American", "British", "Hindi", "Telugu", "Other"]
    assert f["emotion"]["choices"] == ["Neutral", "Happy", "Sad", "Angry", "Excited", "Calm", "Fearful", "Serious"]
    assert v["configured"] is False and "Voice provider is not configured" in v["config_message"]
    assert refine(client, h, "voice", "").status_code == 422        # text is required


def test_voice_refinement_makes_no_llm_call_and_keeps_text_verbatim(client, make_user, monkeypatch):
    llm = use_llm(monkeypatch)
    h, _ = make_user()
    r = refine(client, h, "voice", 'Say this in a calm but emotional voice: "The kingdom will rise again."', options={"gender": "Female"}).json()
    assert llm.requests == []                                                # 1 provider generation, zero LLM calls
    assert r["refined_prompt"] == "The kingdom will rise again." and r["metadata"]["method"] == "local"
    assert r["metadata"]["options"]["emotion"] == "Calm" and any("Emotion set to Calm" in w for w in r["metadata"]["warnings"])
    r = refine(client, h, "voice", "Just read this line.", options={"emotion": "Sad"}).json()
    assert r["refined_prompt"] == "Just read this line." and r["metadata"]["options"]["emotion"] == "Sad"


def test_voice_success_with_mocked_provider(client, make_user, monkeypatch):
    llm = use_llm(monkeypatch)
    voice = use_voice(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    opts = {"gender": "Male", "accent": "British", "emotion": "Sad"}
    job = generate_and_run(client, h, "voice", prompt="Farewell, my king.", refined="Farewell, my king.", options=opts, project_id=pid)
    assert job["status"] == "COMPLETED" and llm.requests == [] and job["provider"] == "google"
    req = voice.requests[0]
    body = json.loads(req.content)
    assert req.headers["x-goog-api-key"] == "test-voice-key" and "test-voice-key" not in str(req.url) and "key=" not in str(req.url)
    assert body["input"]["text"] == "Farewell, my king." and body["voice"] == {"languageCode": "en-GB", "name": "en-GB-Neural2-B"}
    assert body["audioConfig"]["audioEncoding"] == "MP3" and body["audioConfig"]["speakingRate"] < 1 and body["audioConfig"]["pitch"] < 0
    [a] = assets(client, h, pid, "VOICE")
    assert a["format"] == "mp3" and a["mime_type"] == "audio/mpeg" and a["language"] == "English" and a["has_file"]
    assert a["meta"]["gender"] == "Male" and a["meta"]["accent"] == "British" and a["meta"]["emotion"] == "Sad" and a["meta"]["voice"] == "en-GB-Neural2-B"
    assert any("approximated" in n for n in a["meta"]["notes"])              # honest about what the provider can't do
    assert client.get(a["url"], headers=h).content == MP3
    assert used(client, h, "voice") == 1


def test_voice_hindi_and_telugu_use_matching_voices(client, make_user, monkeypatch):
    voice = use_voice(monkeypatch)
    h, _ = make_user()
    generate_and_run(client, h, "voice", prompt="नमस्ते", options={"gender": "Female", "language": "Hindi"})
    generate_and_run(client, h, "voice", prompt="నమస్కారం", options={"gender": "Male", "accent": "Telugu"})
    names = [json.loads(r.content)["voice"] for r in voice.requests]
    assert names == [{"languageCode": "hi-IN", "name": "hi-IN-Neural2-A"}, {"languageCode": "te-IN", "name": "te-IN-Standard-B"}]


def test_voice_unsupported_language_is_explained_not_faked(client, make_user, monkeypatch):
    voice = use_voice(monkeypatch)
    monkeypatch.setattr(GoogleTTSProvider, "supported_locales", lambda self: ["en-IN", "en-US", "en-GB", "hi-IN"])   # provider without Telugu
    h, _ = make_user()
    r = refine(client, h, "voice", "నమస్కారం", options={"language": "Telugu"})
    assert r.status_code == 422 and "doesn't support Telugu" in r.json()["error"]["message"] and "English, Hindi" in r.json()["error"]["message"]
    r = generate(client, h, "voice", "నమస్కారం", options={"accent": "Telugu"})
    assert r.status_code == 422 and used(client, h, "voice") == 0 and voice.requests == []


def test_voice_falls_back_for_unsupported_attributes_with_notes(client, make_user, monkeypatch):
    voice = use_voice(monkeypatch)
    h, _ = make_user()
    r = refine(client, h, "voice", "Hello there", options={"accent": "Other", "emotion": "Angry"}).json()
    assert any("American English" in w for w in r["metadata"]["warnings"])
    generate_and_run(client, h, "voice", prompt="Hello there", options={"accent": "Other"})
    body = json.loads(voice.requests[0].content)
    assert body["voice"]["languageCode"] == "en-US"                          # closest supported accent; default gender female
    assert client.get("/api/jobs", headers=h).json()[0]["status"] == "COMPLETED"


def test_voice_text_too_long_for_provider(client, make_user, monkeypatch):
    use_voice(monkeypatch)
    h, _ = make_user()
    r = generate(client, h, "voice", "क" * 1700, options={"language": "Hindi"})     # 3 bytes each -> over the provider's byte limit
    assert r.status_code == 422 and "too long" in r.json()["error"]["message"]


def test_voice_provider_not_configured(client, make_user):
    h, _ = make_user()
    job = generate_and_run(client, h, "voice", prompt="Hello", options={"gender": "Female"})
    assert job["status"] == "FAILED" and job["error_code"] == "API_NOT_CONFIGURED"
    assert "Voice provider is not configured" in job["error_message"] and used(client, h, "voice") == 0


def test_voice_provider_failures(client, make_user, monkeypatch):
    h, _ = make_user()
    use_voice(monkeypatch, handler=lambda req: httpx.Response(403, json={"error": {"message": "API key not valid"}}))
    j = generate_and_run(client, h, "voice", prompt="Hello")
    assert (j["status"], j["error_code"], j["attempts"]) == ("FAILED", "AUTHENTICATION_ERROR", 1) and "API key not valid" not in json.dumps(j)
    use_voice(monkeypatch, handler=lambda req: httpx.Response(500, json={}))
    r = generate(client, h, "voice", "Hello again")
    run_all()
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert j["status"] == "FAILED" and j["attempts"] == 2
    assert j["error_message"] == "Voice generation failed because the configured voice provider is unavailable."


def test_voice_retries_without_pitch_when_voice_rejects_it(client, make_user, monkeypatch):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        if "pitch" in calls[-1]["audioConfig"]:
            return httpx.Response(400, json={"error": {"message": "This voice does not support pitch parameters"}})
        return httpx.Response(200, json={"audioContent": base64.b64encode(MP3).decode()})

    use_voice(monkeypatch, handler=handler)
    h, _ = make_user()
    j = generate_and_run(client, h, "voice", prompt="Hello", options={"emotion": "Happy"})
    assert j["status"] == "COMPLETED" and len(calls) == 2 and "pitch" not in calls[1]["audioConfig"]


def test_voice_from_selected_script_scene_text(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    voice = use_voice(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "script", project_id=pid)
    scr = client.get(f"/api/projects/{pid}/assets?type=SCRIPT", headers=h).json()[0]
    d = client.get(f"/api/assets/{scr['id']}", headers=h).json()
    scene2 = d["meta"]["scenes"][1]
    excerpt = d["text_content"][scene2["start"]:scene2["end"]][:400]
    j = generate_and_run(client, h, "voice", prompt=excerpt, project_id=pid, options={"gender": "Female"})
    assert j["status"] == "COMPLETED" and json.loads(voice.requests[0].content)["input"]["text"] == excerpt.strip()


def test_default_form_values_are_valid_regression(client, make_user):
    """The Create form submits each field's default. They must be real values (a positional-argument slip once made the music
    duration default the help text)."""
    h, _ = make_user()
    for g in client.get("/api/generate/schema", headers=h).json()["generators"]:
        for f in g["fields"]:
            if f["kind"] == "duration":
                assert f["default"] == 10 and f["choices"] and all(c.isdigit() for c in f["choices"]), g["id"]
            if f["default"] is not None and f["kind"] == "select":
                assert f["default"] in f["choices"], (g["id"], f["key"])
    body = {"generator_type": "music", "prompt": "epic theme", "options": {"duration_seconds": 10}}
    assert client.post("/api/generate/refine", headers=h, json=body).status_code == 200
