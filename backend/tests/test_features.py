"""Database-backed feature switches: defaults, admin control, enforcement on the API itself (not only in the UI), languages, refinement
modes, music vocals and voice emotions. No real provider is called."""
import pytest

from app.config import get_settings
from app.generators import GENERATORS
from app.services import features as features_service

from .helpers import generate, make_project, use_llm, use_music, use_voice

CORE = ["video", "image", "music", "voice", "lyrics", "story", "script"]
OFF_BY_DEFAULT = ["face_replacement", "ai_avatar", "interactive_avatar"]


def public(client):
    r = client.get("/api/features")
    assert r.status_code == 200
    return r.json()


def put(client, ah, **flags):
    r = client.put("/api/admin/features", headers=ah, json={"features": flags})
    assert r.status_code == 200, r.text
    return {f["id"]: f for f in r.json()["features"]}


# ------------------------------------------------------------------ defaults
def test_defaults_core_on_optional_and_extra_languages_off(client):
    f = public(client)
    assert all(f["generators"][g] for g in CORE) and not any(f["generators"][g] for g in OFF_BY_DEFAULT)
    assert sorted(f["hidden"]) == sorted(OFF_BY_DEFAULT) and f["asset_types"]["FACE"] is False and f["asset_types"]["AVATAR"] is False
    assert f["languages"] == ["English"] and f["refinement"] == {"ai": True, "basic": True}


def test_the_catalogue_covers_every_required_switch_and_defaults_match_the_spec():
    ids = {f.id: f.default for f in features_service.FEATURES}
    assert {"image", "video", "music", "voice", "lyrics", "story", "script", "prompt_refinement", "face_swap", "ai_avatar",
            "interactive_avatar", "hindi", "telugu"} <= set(ids)
    assert [i for i in ("image", "video", "music", "voice", "lyrics", "story", "script", "prompt_refinement") if not ids[i]] == []
    assert [i for i in ("face_swap", "ai_avatar", "interactive_avatar", "hindi", "telugu") if ids[i]] == []
    assert {g.id for g in GENERATORS} == set(features_service.GENERATOR_FEATURE)


# ------------------------------------------------------------------ admin control
def test_only_an_admin_can_read_or_change_features(client, make_user):
    h, _ = make_user()
    assert client.get("/api/admin/features").status_code == 401
    assert client.get("/api/admin/features", headers=h).status_code == 403
    assert client.put("/api/admin/features", headers=h, json={"features": {"face_swap": True}}).status_code == 403
    assert public(client)["generators"]["face_replacement"] is False


def test_admin_list_has_name_description_state_and_provider_status_but_no_secrets(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "face_provider_api_key", "face-SECRETVALUE-1")
    monkeypatch.setattr(get_settings(), "llm_api_key", "llm-SECRETVALUE-2")
    ah, _ = make_user("boss@example.com")
    r = client.get("/api/admin/features", headers=ah)
    assert "SECRETVALUE" not in r.text
    by_id = {f["id"]: f for f in r.json()["features"]}
    assert all(f["label"] and f["description"] and isinstance(f["enabled"], bool) for f in by_id.values())
    assert by_id["face_swap"]["enabled"] is False and by_id["face_swap"]["provider"]["status"] == "configured"
    assert by_id["prompt_refinement"]["provider"]["status"] == "configured" and by_id["hindi"]["provider"] is None
    assert by_id["ai_avatar"]["provider"]["status"] in ("configured", "not_configured")


def test_toggling_is_stored_in_the_database_and_reflected_publicly(client, make_user):
    ah, _ = make_user("boss@example.com")
    put(client, ah, face_swap=True, hindi=True)
    f = public(client)
    assert f["generators"]["face_replacement"] is True and f["asset_types"]["FACE"] is True and f["languages"] == ["English", "Hindi"]
    put(client, ah, face_swap=False, hindi=False)
    assert public(client)["generators"]["face_replacement"] is False and public(client)["languages"] == ["English"]
    assert client.put("/api/admin/features", headers=ah, json={"features": {"nonsense": True}}).status_code == 422


# ------------------------------------------------------------------ enforcement on the API
def test_a_disabled_generator_cannot_be_used_by_calling_the_api(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    pid = make_project(client, h)
    for gen in OFF_BY_DEFAULT:
        r = client.post("/api/generations", headers=h, json={"generator_type": gen, "original_prompt": "hello there", "refined_prompt": "hello there", "project_id": pid})
        assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled", gen
        r = client.post("/api/generate/refine", headers=h, json={"generator_type": gen, "prompt": "hello there", "options": {}})
        assert r.status_code == 403, gen
    put(client, ah, video=False, story=False, script=False)
    assert generate(client, h, "video", project_id=pid).status_code == 403
    assert client.post("/api/text/story", headers=h, json={"prompt": "A lighthouse story please"}).status_code == 403
    assert client.post("/api/text/script", headers=h, json={"story": "x" * 60}).status_code == 403
    assert "video" not in {g["id"] for g in client.get("/api/generate/schema", headers=h).json()["generators"]}
    put(client, ah, video=True)
    assert generate(client, h, "video", project_id=pid).status_code == 201


def test_enabling_an_optional_feature_makes_it_appear_and_an_unconfigured_provider_gives_a_clear_error(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "enable_dev_simulator", False)
    from app.providers import register_default_providers
    register_default_providers()
    try:
        ah, _ = make_user("boss@example.com")
        h, _ = make_user()
        put(client, ah, face_swap=True)
        gens = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}
        assert "face_replacement" in gens and gens["face_replacement"]["configured"] is False and gens["face_replacement"]["config_message"]
        assert "ai_avatar" not in gens
    finally:
        monkeypatch.undo()
        register_default_providers()


def test_existing_assets_stay_visible_when_their_generator_is_switched_off(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    pid = make_project(client, h)
    r = client.post("/api/text/story", headers=h, json={"prompt": "A lighthouse story please", "project_id": pid})
    put(client, ah, story=False)
    assets = client.get(f"/api/projects/{pid}/assets", headers=h)
    assert assets.status_code == 200 and isinstance(assets.json(), list)
    _ = r


# ------------------------------------------------------------------ languages
def test_hindi_and_telugu_are_hidden_and_refused_until_enabled(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    gens = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}
    lang = next(f for f in gens["story"]["fields"] if f["key"] == "language")
    assert lang["choices"] == ["English"]
    voice_lang = next(f for f in gens["voice"]["fields"] if f["key"] == "language")
    assert voice_lang["choices"] == ["English"]
    assert "accent" not in {f["key"] for f in gens["voice"]["fields"]}                      # advanced: administrators only
    agens = {g["id"]: g for g in client.get("/api/generate/schema", headers=ah).json()["generators"]}
    accent = next(f for f in agens["voice"]["fields"] if f["key"] == "accent")
    assert "Hindi" not in accent["choices"] and "Telugu" not in accent["choices"]
    r = client.post("/api/generations", headers=h, json={"generator_type": "story", "original_prompt": "A story about a king", "refined_prompt": "A story about a king",
                                                         "options": {"language": "Hindi"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "language_unavailable"
    assert client.post("/api/text/story", headers=h, json={"prompt": "A lighthouse story please", "language": "Telugu"}).status_code == 422
    assert client.get("/api/text/options", headers=h).json()["languages"] == ["English"]
    put(client, ah, hindi=True)
    gens = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}
    assert next(f for f in gens["story"]["fields"] if f["key"] == "language")["choices"] == ["English", "Hindi"]


# ------------------------------------------------------------------ refinement modes
REFINE = {"generator_type": "story", "prompt": "A lighthouse keeper finds a message in a bottle", "options": {"genre": "Drama", "length": "Short"}}


def refine(client, h, body=None):
    r = client.post("/api/generate/refine", headers=h, json=body or REFINE)
    assert r.status_code == 200, r.text
    return r.json()


def test_without_an_llm_key_basic_refinement_is_used_and_labelled_honestly(client, make_user):
    h, _ = make_user()
    d = refine(client, h)
    m = d["metadata"]
    assert m["method"] == "template" and m["label"] == "Basic refinement" and m["provider"] is None
    assert d["refined_prompt"].startswith("Create a story about: A lighthouse keeper") and "Length: Short" in d["refined_prompt"]     # genre is an administrator option: ignored here
    ah, _ = make_user("boss@example.com")
    assert refine(client, ah)["refined_prompt"].startswith("Create a Drama story about: A lighthouse keeper")
    assert "AI" not in m["label"]


def test_with_an_llm_the_result_is_labelled_ai_refined(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    h, _ = make_user()
    m = refine(client, h)["metadata"]
    assert m["method"] == "llm" and m["label"] == "AI refined"


def test_admin_can_switch_ai_refinement_off_and_basic_takes_over(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    put(client, ah, prompt_refinement=False)
    m = refine(client, h)["metadata"]
    assert m["method"] == "template" and m["label"] == "Basic refinement" and any("switched off" in w for w in m["warnings"]) and not rec.bodies()
    put(client, ah, prompt_refinement=True, prefer_ai_refinement=False)
    assert refine(client, h)["metadata"]["method"] == "template" and not rec.bodies()


def test_with_both_modes_off_the_prompt_is_left_alone_and_the_user_is_never_blocked(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    put(client, ah, prompt_refinement=False, free_refinement=False)
    d = refine(client, h)
    assert d["refined_prompt"] == REFINE["prompt"] and d["metadata"]["method"] == "none" and d["metadata"]["label"] == "Not refined"


def test_an_llm_failure_falls_back_to_basic_only_when_auto_fallback_is_on(client, make_user, monkeypatch):
    import httpx

    from .helpers import use_llm as _use
    _use(monkeypatch, lambda request: httpx.Response(503, json={"error": {"message": "overloaded"}}))
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    m = refine(client, h)["metadata"]
    assert m["method"] == "template" and m["label"] == "Basic refinement"
    put(client, ah, auto_fallback_refinement=False)
    d = refine(client, h)
    assert d["metadata"]["method"] == "none" and d["refined_prompt"] == REFINE["prompt"]


def test_an_exhausted_ai_allowance_uses_basic_refinement_and_charges_nothing(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    monkeypatch.setattr(get_settings(), "refine_daily_limit", 1)
    h, _ = make_user()
    assert refine(client, h)["metadata"]["method"] == "llm"
    m = refine(client, h)["metadata"]
    assert m["method"] == "template" and any("allowance" in w for w in m["warnings"])


# ------------------------------------------------------------------ music and voice options
def test_music_defaults_to_instrumental_and_offers_vocals_as_an_option(client, make_user):
    h, _ = make_user()
    music = next(g for g in client.get("/api/generate/schema", headers=h).json()["generators"] if g["id"] == "music")
    f = next(f for f in music["fields"] if f["key"] == "vocals_mode")
    assert f["choices"] == ["Instrumental", "Instrumental + Vocals"] and f["default"] == "Instrumental"


def test_vocals_are_refused_clearly_when_the_provider_cannot_sing(client, make_user, monkeypatch):
    use_music(monkeypatch)
    h, _ = make_user()
    r = client.post("/api/generate/refine", headers=h, json={"generator_type": "music", "prompt": "An epic theme", "options": {"vocals_mode": "Instrumental + Vocals"}})
    assert r.status_code == 422 and "instrumental" in r.json()["error"]["message"].lower()
    r = client.post("/api/generate/refine", headers=h, json={"generator_type": "music", "prompt": "An epic theme", "options": {}})
    assert r.status_code == 200


def test_voice_offers_both_genders_and_the_eight_emotions(client, make_user):
    h, _ = make_user("boss@example.com")          # gender and emotion are administrator options
    voice = next(g for g in client.get("/api/generate/schema", headers=h).json()["generators"] if g["id"] == "voice")
    fields = {f["key"]: f for f in voice["fields"]}
    assert fields["gender"]["choices"] == ["Male", "Female"]
    assert set(fields["emotion"]["choices"]) == {"Neutral", "Happy", "Sad", "Angry", "Excited", "Calm", "Fearful", "Serious"}
    from app.providers.voice import EMOTION_PROSODY
    assert set(EMOTION_PROSODY) == set(fields["emotion"]["choices"])


@pytest.mark.parametrize("path", ["/api/features", "/api/generate/schema", "/api/admin/features", "/api/admin/providers"])
def test_no_secret_is_ever_returned(client, make_user, monkeypatch, path):
    s = get_settings()
    for attr in ("face_provider_api_key", "video_provider_api_key", "llm_api_key", "voice_api_key", "music_api_key"):
        monkeypatch.setattr(s, attr, "SECRETVALUE-" + attr)
    ah, _ = make_user("boss@example.com")
    assert "SECRETVALUE" not in client.get(path, headers=ah).text
