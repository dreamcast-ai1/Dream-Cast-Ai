"""Optional paid features (face replacement, AI avatar, interactive avatar) are hidden while no provider is configured and come back by
themselves once credentials exist. The code for them is never removed; core features are never affected."""
import pytest

from app.config import get_settings
from app.generators import OPTIONAL_GENERATORS
from app.providers import register_default_providers

OPTIONAL = ["face_replacement", "ai_avatar", "interactive_avatar"]
CORE = ["video", "image", "music", "voice", "lyrics", "story", "script"]


@pytest.fixture
def no_simulator(monkeypatch):
    """Production-like: the development simulator (the only thing that serves avatars) is not registered."""
    monkeypatch.setattr(get_settings(), "enable_dev_simulator", False)
    register_default_providers()
    yield
    monkeypatch.undo()
    register_default_providers()


def features(client):
    r = client.get("/api/features")                       # public: the landing page uses it before anyone signs in
    assert r.status_code == 200
    return r.json()


def test_the_optional_set_is_exactly_the_three_paid_features():
    assert OPTIONAL_GENERATORS == frozenset(OPTIONAL)


def test_without_credentials_the_optional_features_are_hidden_and_core_ones_stay(client, no_simulator):
    f = features(client)
    assert [g for g in OPTIONAL if f["generators"][g]] == [] and sorted(f["hidden"]) == sorted(OPTIONAL)
    assert all(f["generators"][g] for g in CORE)                                          # core generators are never hidden, keys or not
    assert f["asset_types"]["FACE"] is False and f["asset_types"]["AVATAR"] is False
    assert all(f["asset_types"][t] for t in ("VIDEO", "IMAGE", "MUSIC", "VOICE", "LYRICS", "STORY", "SCRIPT"))


def test_setting_the_face_key_brings_face_replacement_back_with_no_code_change(client, no_simulator, monkeypatch):
    monkeypatch.setattr(get_settings(), "face_provider_api_key", "test-face-key")
    f = features(client)
    assert f["generators"]["face_replacement"] is True and f["asset_types"]["FACE"] is True
    assert f["generators"]["ai_avatar"] is False and f["generators"]["interactive_avatar"] is False and f["asset_types"]["AVATAR"] is False


def test_placeholder_credentials_do_not_count(client, no_simulator):
    from app.config import Settings
    assert Settings(_env_file=None, face_provider_api_key="REPLACE_WITH_FAL_AI_KEY").face_provider_api_key == ""
    assert features(client)["generators"]["face_replacement"] is False


def test_the_development_simulator_keeps_avatars_visible_only_while_it_is_on(client, no_simulator, monkeypatch):
    assert features(client)["generators"]["ai_avatar"] is False
    monkeypatch.setattr(get_settings(), "enable_dev_simulator", True)
    register_default_providers()
    f = features(client)
    assert f["generators"]["ai_avatar"] and f["generators"]["interactive_avatar"] and f["asset_types"]["AVATAR"]


def test_an_admin_can_switch_a_provider_off_and_its_feature_disappears(client, make_user, no_simulator, monkeypatch):
    monkeypatch.setattr(get_settings(), "face_provider_api_key", "test-face-key")
    ah, _ = make_user("boss@example.com")
    assert features(client)["generators"]["face_replacement"] is True
    assert client.put("/api/admin/providers/fal-face", headers=ah, json={"enabled": False}).status_code == 200
    assert features(client)["generators"]["face_replacement"] is False
    client.put("/api/admin/providers/fal-face", headers=ah, json={"enabled": True})
    assert features(client)["generators"]["face_replacement"] is True


def test_video_and_image_do_not_depend_on_the_face_key(client, make_user, no_simulator):
    h, _ = make_user()
    assert features(client)["generators"]["video"] and features(client)["generators"]["image"]       # even though no fal key at all is set here
    gens = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}
    assert "image" in gens and "video" in gens and "face_replacement" in gens          # the backend definition is intact (nothing removed)


def test_the_hidden_features_still_exist_in_the_backend_and_answer_cleanly(client, make_user, no_simulator):
    """Hiding is a UI matter: the code stays, and a direct API call gets the same clear answer as before (never a crash)."""
    h, _ = make_user()
    r = client.post("/api/generations", headers=h, json={"generator_type": "face_replacement", "original_prompt": "x", "refined_prompt": "x", "options": {}})
    assert r.status_code in (422, 503) and r.json()["error"]["message"] and "Traceback" not in r.text
    r = client.post("/api/generations", headers=h, json={"generator_type": "ai_avatar", "original_prompt": "hello there", "refined_prompt": "hello there", "options": {}})
    assert r.status_code == 503 and r.json()["error"]["message"]


def test_the_features_endpoint_returns_only_booleans_and_names(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "face_provider_api_key", "face-SECRETVALUE-1")
    monkeypatch.setattr(s, "video_provider_api_key", "video-SECRETVALUE-2")
    body = client.get("/api/features").text
    assert "SECRETVALUE" not in body and s.auth_secret_key not in body
    data = client.get("/api/features").json()
    assert set(data) == {"generators", "asset_types", "hidden"} and all(isinstance(v, bool) for v in data["generators"].values())
