import pytest
import json

from app.providers import registry

from .helpers import (LYRICS, SCRIPT, STORY, generate, generate_and_run, llm_reply, make_project, run_all, use_llm, used)

pytestmark = pytest.mark.usefixtures("all_features")


def refine(client, h, gen, prompt="A warrior discovers a hidden kingdom.", **kw):
    return client.post("/api/generate/refine", headers=h, json={"generator_type": gen, "prompt": prompt, **kw})


def assets(client, h, pid, type_=""):
    return client.get(f"/api/projects/{pid}/assets" + (f"?type={type_}" if type_ else ""), headers=h).json()


def gen_calls(rec):
    """LLM calls made for generation (as opposed to prompt refinement)."""
    return [b for b in rec.bodies() if "Use exactly this structure" in b["messages"][0]["content"] or "story OUTLINE" in b["messages"][0]["content"]
            or "songwriter" in b["messages"][0]["content"]]


# ------------------------------------------------------------------ registry
def test_simulator_no_longer_serves_the_five_real_generators():
    sim = registry.get("dev-simulator")
    assert sim.generators.isdisjoint({"story", "script", "lyrics", "music", "voice", "video", "face_replacement"})
    assert {"ai_avatar", "interactive_avatar"} == set(sim.generators)     # video and face now have real providers
    for gen in ("story", "script", "lyrics", "music", "voice"):
        assert [p.name for p in registry.for_generator(gen)] and not any(p.simulated for p in registry.for_generator(gen))


# ------------------------------------------------------------------ story
def test_story_schema_options(client, make_user):
    h, _ = make_user()
    story = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}["story"]
    fields = {f["key"]: f for f in story["fields"]}
    assert fields["genre"]["choices"] == ["Action", "Adventure", "Comedy", "Drama", "Romance", "Thriller", "Horror", "Sci-Fi",
                                          "Fantasy", "Mystery", "Crime", "Historical", "Custom"]
    assert fields["language"]["choices"] == ["English", "Hindi", "Telugu"]
    assert fields["length"]["choices"] == ["Short", "Medium", "Long", "Custom"] and fields["length"]["allow_custom"]


def test_story_validation(client, make_user):
    h, _ = make_user()
    assert refine(client, h, "story", prompt="").status_code == 422
    assert refine(client, h, "story", options={"language": "Klingon"}).status_code == 422
    assert refine(client, h, "story", options={"genre": "Nonsense"}).status_code == 422
    assert generate(client, h, "story", options={"language": "French"}).status_code == 422
    assert refine(client, h, "story", options={"genre": "Custom", "genre_custom": "Space western", "language": "Telugu"}).status_code == 200


def test_story_generation_end_to_end_with_project(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    body = {"generator_type": "story", "prompt": "A warrior discovers a hidden kingdom.", "project_id": pid,
            "options": {"genre": "Fantasy", "language": "English", "length": "Medium"}}
    ref = client.post("/api/generate/refine", headers=h, json=body).json()
    assert ref["metadata"]["method"] == "llm"
    job = generate_and_run(client, h, "story", options=ref["metadata"]["options"], project_id=pid, refined=ref["refined_prompt"])
    assert job["status"] == "COMPLETED" and job["provider"] == "groq-text" and job["simulated"] is False
    [a] = assets(client, h, pid, "STORY")
    assert a["title"] == "The Hidden Kingdom" and a["version"] == 1 and a["status"] == "READY" and a["project_id"] == pid
    assert a["language"] == "English" and a["format"] == "txt" and a["has_file"] is False
    detail = client.get(f"/api/assets/{a['id']}", headers=h).json()
    assert detail["text_content"] == STORY and detail["meta"]["word_count"] > 30 and detail["job_id"] == job["id"]
    # exactly one refinement call + one generation call
    assert len(rec.requests) == 2 and len(gen_calls(rec)) == 1
    system = gen_calls(rec)[0]["messages"][0]["content"]
    assert "ACT 1" in system and "ORIGINAL" in system and "about 600 words" in system
    n = client.get("/api/notifications", headers=h).json()["items"][0]
    assert n["type"] == "job_completed" and n["project_id"] == pid and n["job_id"] == job["id"]
    assert used(client, h, "story") == 1


def test_story_language_instructions_and_custom_length(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    h, _ = make_user()
    generate_and_run(client, h, "story", options={"language": "Telugu", "length": "Custom", "length_custom": "about 250 words"})
    generate_and_run(client, h, "story", options={"language": "Hindi"})
    systems = [b["messages"][0]["content"] for b in gen_calls(rec)]
    assert "Telugu (Telugu script)" in systems[0] and "about 250 words" in systems[0]
    assert "Hindi (Devanagari script)" in systems[1]
    assert all("TITLE:" in s for s in systems)


def test_story_without_project_stays_on_job(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    h, _ = make_user()
    job = generate_and_run(client, h, "story")
    assert job["status"] == "COMPLETED" and job["assets"] == [] and job["output"]["text"] == STORY


def test_text_provider_not_configured_fails_gracefully_and_refunds(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    schema = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}["story"]
    assert schema["configured"] is False and schema["config_message"] == "Text generation provider is not configured." and schema["available"]
    job = generate_and_run(client, h, "story", project_id=pid)
    assert job["status"] == "FAILED" and job["error_code"] == "API_NOT_CONFIGURED"
    assert job["error_message"] == "Text generation provider is not configured."
    assert assets(client, h, pid) == [] and used(client, h, "story") == 0
    assert client.get("/api/notifications", headers=h).json()["items"][0]["type"] == "job_failed"


def test_text_provider_outage_retries_once_then_reports_useful_error(client, make_user, monkeypatch):
    use_llm(monkeypatch, llm_reply("x", status=500))
    h, _ = make_user()
    r = generate(client, h, "story")
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert j["status"] == "RETRYING" and j["attempts"] == 1
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert j["status"] == "FAILED" and j["attempts"] == 2 and j["error_code"] == "PROVIDER_UNAVAILABLE"
    assert j["error_message"] == ("Story generation failed because the text provider is temporarily overloaded or unavailable. "
                                  "DreamCast already retried automatically; please try again in a minute.")


def test_text_provider_bad_key_is_not_retried(client, make_user, monkeypatch):
    use_llm(monkeypatch, llm_reply("x", status=401))
    h, _ = make_user()
    job = generate_and_run(client, h, "lyrics", options={})
    assert job["status"] == "FAILED" and job["attempts"] == 1 and job["error_code"] == "AUTHENTICATION_ERROR"
    assert "test-llm-key" not in json.dumps(job)


def test_empty_llm_output_is_a_failure_not_a_blank_asset(client, make_user, monkeypatch):
    use_llm(monkeypatch, llm_reply("ok"))
    h, _ = make_user()
    pid = make_project(client, h)
    job = generate_and_run(client, h, "story", project_id=pid)
    assert job["status"] in ("RETRYING", "FAILED") and assets(client, h, pid) == []


# ------------------------------------------------------------------ script
def _story(client, h, monkeypatch, pid):
    generate_and_run(client, h, "story", project_id=pid)
    return assets(client, h, pid, "STORY")[0]["id"]


def test_script_uses_selected_story_as_context(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    story_id = _story(client, h, monkeypatch, pid)
    opts = {"genre": "Fantasy", "language": "English", "length": "Medium", "target_minutes": "10 minutes", "story_asset_id": story_id}
    ref = client.post("/api/generate/refine", headers=h, json={"generator_type": "script", "prompt": "Turn the story into a script",
                                                                 "project_id": pid, "options": opts}).json()
    assert ref["metadata"]["context_used"]["story"] is True
    job = generate_and_run(client, h, "script", prompt="Turn the story into a script", options=opts, project_id=pid, refined=ref["refined_prompt"])
    assert job["status"] == "COMPLETED"
    call = [b for b in gen_calls(rec) if "SCENE 01" in b["messages"][0]["content"]][0]
    user = call["messages"][1]["content"]
    assert "STORY TO ADAPT" in user and "Kael follows a strange light" in user            # the whole story, no copy/paste needed
    assert "about 10 minutes of screen time" in call["messages"][0]["content"]
    [s] = assets(client, h, pid, "SCRIPT")
    d = client.get(f"/api/assets/{s['id']}", headers=h).json()
    assert d["project_id"] == pid and d["title"] == "The Hidden Kingdom" and d["text_content"] == SCRIPT
    assert [(x["number"], x["heading"]) for x in d["meta"]["scenes"]] == [(1, "EXT. MOUNTAIN PASS - NIGHT"), (2, "INT. SLEEPING CITY - NIGHT"), (3, "EXT. TOWER - DAWN")]
    assert d["meta"]["options"]["story_asset_id"] == story_id


def test_script_rejects_foreign_or_wrong_story(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    h, _ = make_user()
    h2, _ = make_user("b@example.com")
    pid, pid_other = make_project(client, h), make_project(client, h, "Other")
    story_id = _story(client, h, monkeypatch, pid)
    body = lambda **kw: {"generator_type": "script", "prompt": "script it", **kw}
    assert client.post("/api/generate/refine", headers=h, json=body(project_id=pid_other, options={"story_asset_id": story_id})).status_code == 422
    assert client.post("/api/generate/refine", headers=h, json=body(options={"story_asset_id": story_id})).status_code == 422   # needs a project
    assert client.post("/api/generate/refine", headers=h2, json=body(project_id=pid, options={"story_asset_id": story_id})).status_code == 404
    assert client.post("/api/generate/refine", headers=h, json=body(project_id=pid, options={"story_asset_id": "nope"})).status_code == 422
    lyr = generate_and_run(client, h, "lyrics", project_id=pid)
    lyrics_id = assets(client, h, pid, "LYRICS")[0]["id"]
    assert client.post("/api/generate/refine", headers=h, json=body(project_id=pid, options={"story_asset_id": lyrics_id})).status_code == 422   # wrong type
    assert lyr["status"] == "COMPLETED"


def test_script_max_output_is_capped_and_thirty_minutes_is_allowed(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    h, _ = make_user()
    r = refine(client, h, "script", prompt="epic saga", options={"target_minutes": "30 minutes"})
    assert r.status_code == 200
    generate_and_run(client, h, "script", options={"target_minutes": "30 minutes", "language": "Telugu"})
    call = gen_calls(rec)[0]
    assert call["max_tokens"] <= 6000 and "30 minutes of screen time, condensed into 15 scenes" in call["messages"][0]["content"]
    assert refine(client, h, "script", options={"target_minutes": "45 minutes"}).status_code == 422


def test_script_edit_reparses_scenes_without_calling_ai(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "script", project_id=pid)
    calls_before = len(rec.requests)
    [s] = assets(client, h, pid, "SCRIPT")
    edited = "TITLE: My Cut\n\nSCENE 01\nINT. HALL - DAY\n" + "Action lines that are long enough to count as a real scene body here.\n\nSCENE 02\nEXT. ROOF - NIGHT\n" + "More action lines long enough to count as a real scene body as well."
    d = client.put(f"/api/assets/{s['id']}", headers=h, json={"text_content": edited}).json()
    assert d["text_content"] == edited and d["title"] == "My Cut" and d["meta"]["edited"] is True
    assert [x["number"] for x in d["meta"]["scenes"]] == [1, 2] and d["updated_at"] >= d["created_at"]
    assert len(rec.requests) == calls_before                                 # editing never calls the AI


# ------------------------------------------------------------------ lyrics
def test_lyrics_generation_language_and_structure(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    schema = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}["lyrics"]
    assert [f["key"] for f in schema["fields"]] == ["language"]              # deliberately simple: prompt + language only
    job = generate_and_run(client, h, "lyrics", prompt="A song about a battle at dawn", options={"language": "Hindi"}, project_id=pid)
    assert job["status"] == "COMPLETED"
    system = gen_calls(rec)[0]["messages"][0]["content"]
    assert "Hindi (Devanagari script)" in system and "[VERSE 1]" in system and "[FINAL CHORUS]" in system and "Never reproduce" in system
    [a] = assets(client, h, pid, "LYRICS")
    assert a["title"] == "Battle Dawn" and a["language"] == "Hindi"
    assert client.get(f"/api/assets/{a['id']}", headers=h).json()["text_content"] == LYRICS
    assert refine(client, h, "lyrics", options={"language": "Tamil"}).status_code == 422


# ------------------------------------------------------------------ general
def test_monthly_limits_apply_to_text_generators(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    h, _ = make_user()
    codes = [generate(client, h, "script").status_code for _ in range(11)]
    assert codes == [201] * 10 + [429]                                      # script: 10/month
    assert [generate(client, h, "lyrics").status_code for _ in range(21)] == [201] * 20 + [429]   # lyrics: 20/month


def test_admin_can_disable_text_provider(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    name = next(p["name"] for p in client.get("/api/admin/providers", headers=ah).json()["providers"] if p["generators"] == ["lyrics", "script", "story"])
    client.put(f"/api/admin/providers/{name}", json={"enabled": False}, headers=ah)
    r = generate(client, h, "story")
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_UNAVAILABLE" and "disabled" in r.json()["error"]["message"]
    listing = next(p for p in client.get("/api/admin/providers", headers=ah).json()["providers"] if p["name"] == name)
    assert listing["label"] == "Text generation" and listing["info"]["model"] and "test-llm-key" not in json.dumps(listing)


def test_no_api_keys_in_any_response(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    h, _ = make_user()
    text = json.dumps([client.get(u, headers=h).json() for u in ("/api/generate/schema", "/api/settings/providers")])
    assert "test-llm-key" not in text
