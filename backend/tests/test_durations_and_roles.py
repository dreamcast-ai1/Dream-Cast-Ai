"""5/10/15 second durations end to end, advanced options for administrators only, admin-set defaults, capability-aware Pollinations requests and
FFmpeg length fitting for music and speech. Provider HTTP is mocked; nothing real is called."""
import json
import os
import subprocess
import tempfile
from urllib.parse import parse_qs, urlparse

import httpx
import imageio_ffmpeg
import pytest

from app import media
from app.config import get_settings
from app.providers import music as music_module
from app.providers import pollinations as pol_module

from .helpers import _client, generate, make_project, run_all, use_music
from .test_pollinations_knowlez import FakeKnowlez, FakePollinations, KEY_TTS, KEY_VID, job, asset_info, mp3_bytes, used

pytestmark = pytest.mark.usefixtures("all_features")


def tone(ext: str, seconds: float, codec: list[str] | None = None) -> bytes:
    path = os.path.join(tempfile.mkdtemp(), "t" + ext)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", *(codec or []), path], capture_output=True, check=True)
    return open(path, "rb").read()


def schema(client, h):
    return {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}


def keys(g):
    return [f["key"] for f in g["fields"]]


# ------------------------------------------------------------------ who sees what
ADVANCED = {"style", "genre", "mood", "gender", "accent", "emotion", "voice"}


def test_normal_users_see_only_the_simple_options_and_administrators_see_everything(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    user, admin = schema(client, h), schema(client, ah)
    for gid in ("video", "image", "music", "voice", "lyrics", "story", "script"):
        assert not ADVANCED & set(keys(user[gid])), gid
    assert keys(user["video"]) == ["method", "duration_seconds", "aspect_ratio"]
    assert "duration_seconds" in keys(user["music"]) and "duration_seconds" in keys(user["voice"])
    assert {"style"} <= set(keys(admin["video"])) and {"genre", "mood"} <= set(keys(admin["music"]))
    assert {"gender", "accent", "emotion", "voice"} <= set(keys(admin["voice"])) and "genre" in keys(admin["story"])
    assert all(f["advanced"] for g in admin.values() for f in g["fields"] if f["key"] in ADVANCED)


def test_the_duration_choices_are_exactly_5_10_and_15_for_video_music_and_voice(client, make_user):
    h, _ = make_user()
    s = schema(client, h)
    for gid in ("video", "music", "voice"):
        f = next(f for f in s[gid]["fields"] if f["key"] == "duration_seconds")
        assert f["choices"] == ["5", "10", "15"], gid


def test_a_normal_user_cannot_set_advanced_options_by_calling_the_api(client, make_user, monkeypatch):
    fake = FakeKnowlez()
    from app.providers import knowlez as knowlez_module
    monkeypatch.setattr(get_settings(), "knowlez_api_key", KEY_TTS)
    monkeypatch.setattr(knowlez_module, "http_client", _client(fake.handler))
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, "voice", prompt="Hello there friend", options={"gender": "Male", "emotion": "Angry", "accent": "British", "voice": "bm_lewis"}, project_id=pid)
    assert r.status_code == 201
    run_all()
    j = job(client, h, r.json()["job_id"])
    assert j["status"] == "COMPLETED"
    for k in ("gender", "emotion", "accent", "voice"):
        assert k not in j["options"] or j["options"][k] != {"gender": "Male", "emotion": "Angry", "accent": "British", "voice": "bm_lewis"}[k]
    assert json.loads(fake.requests[0].content)["voice"] == "af_bella"                  # the default female voice, not the one that was asked for
    ah, _ = make_user("boss@example.com")
    pid2 = make_project(client, ah)
    r = generate(client, ah, "voice", prompt="Hello there friend", options={"gender": "Male", "accent": "British"}, project_id=pid2)
    run_all()
    assert json.loads(fake.requests[-1].content)["voice"] == "bm_george"                # an administrator's choice is honoured


# ------------------------------------------------------------------ admin defaults
def test_only_administrators_can_read_or_change_the_defaults(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    assert client.get("/api/admin/defaults").status_code == 401
    assert client.get("/api/admin/defaults", headers=h).status_code == 403
    assert client.put("/api/admin/defaults/voice", headers=h, json={"values": {"gender": "Male"}}).status_code == 403
    d = client.get("/api/admin/defaults", headers=ah).json()["generators"]
    voice = next(g for g in d if g["generator"] == "voice")
    assert {"gender", "emotion", "accent", "voice", "duration_seconds"} <= {f["key"] for f in voice["fields"]}
    assert client.put("/api/admin/defaults/voice", headers=ah, json={"values": {"gender": "Robot"}}).status_code == 422
    assert client.put("/api/admin/defaults/voice", headers=ah, json={"values": {"voice": "bad voice!"}}).status_code == 422
    assert client.put("/api/admin/defaults/voice", headers=ah, json={"values": {"nonsense": "x"}}).status_code == 422
    assert client.put("/api/admin/defaults/hologram", headers=ah, json={"values": {}}).status_code == 404


def test_the_defaults_an_administrator_sets_are_what_normal_users_get(client, make_user, monkeypatch):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    assert client.put("/api/admin/defaults/voice", headers=ah, json={"values": {"gender": "Male", "accent": "British", "emotion": "Serious", "duration_seconds": "5"}}).status_code == 200
    assert client.put("/api/admin/defaults/video", headers=ah, json={"values": {"duration_seconds": 5}}).status_code == 200
    assert client.put("/api/admin/defaults/music", headers=ah, json={"values": {"genre": "Ambient", "mood": "Peaceful", "duration_seconds": "15"}}).status_code == 200
    s = schema(client, h)
    assert next(f for f in s["video"]["fields"] if f["key"] == "duration_seconds")["default"] == 5          # the form starts with the administrator's value
    assert next(f for f in s["music"]["fields"] if f["key"] == "duration_seconds")["default"] == 15
    r = client.post("/api/generate/refine", headers=h, json={"generator_type": "music", "prompt": "calm strings", "options": {}}).json()
    o = r["metadata"]["options"]
    assert o["genre"] == "Ambient" and o["mood"] == "Peaceful" and o["duration_seconds"] == 15           # hidden fields were filled in on the server
    fake = FakeKnowlez()
    from app.providers import knowlez as knowlez_module
    monkeypatch.setattr(get_settings(), "knowlez_api_key", KEY_TTS)
    monkeypatch.setattr(knowlez_module, "http_client", _client(fake.handler))
    pid = make_project(client, h)
    generate(client, h, "voice", prompt="Hello there friend", project_id=pid)
    run_all()
    body = json.loads(fake.requests[0].content)
    assert body["voice"] == "bm_george" and body["speed"] < 1.0                                          # British male + Serious, set by the administrator
    client.put("/api/admin/defaults/voice", headers=ah, json={"values": {}})                              # clearing restores the built-in defaults
    assert next(g for g in client.get("/api/admin/defaults", headers=ah).json()["generators"] if g["generator"] == "voice")["fields"][0]["configured"] is False


# ------------------------------------------------------------------ Pollinations: capability-aware parameters
def use_pol(monkeypatch, fake, model=""):
    s = get_settings()
    monkeypatch.setattr(s, "pollinations_video_api_key", KEY_VID)
    monkeypatch.setattr(s, "pollinations_image_api_key", "sk_img_SECRET_123456")
    monkeypatch.setattr(s, "pollinations_video_model", model)
    monkeypatch.setattr(pol_module, "http_client", _client(fake.handler))


@pytest.mark.parametrize("asked,sent,note", [(5, 6, True), (10, 8, True), (15, 8, True)])
def test_pollinations_video_gets_the_nearest_duration_the_model_supports(client, make_user, monkeypatch, asked, sent, note):
    fake = FakePollinations()
    use_pol(monkeypatch, fake)
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "video", prompt="A knight rides", options={"duration_seconds": asked, "aspect_ratio": "16:9", "method": "Text to Video"}, project_id=pid).json()["job_id"]
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "COMPLETED"
    q = parse_qs(urlparse(str(fake.requests[0].url)).query)
    assert q["duration"] == [str(sent)] and "resolution" not in q and "width" not in q and "height" not in q      # video parameters only
    a = asset_info(client, h, j["assets"][0]["id"])
    assert a["meta"]["requested_seconds"] == asked and a["meta"]["provider_seconds"] == sent
    assert a["duration_seconds"] and 1.5 <= a["duration_seconds"] <= 2.5                                       # what was really generated is measured, not assumed
    assert any("closest" in n for n in a["meta"]["notes"]) == note


def test_a_model_with_unknown_capabilities_gets_no_duration_parameter(client, make_user, monkeypatch):
    fake = FakePollinations()
    use_pol(monkeypatch, fake, model="some/new-video-model")
    h, _ = make_user()
    pid = make_project(client, h)
    generate(client, h, "video", prompt="A knight rides", options={"duration_seconds": 15, "aspect_ratio": "9:16", "method": "Text to Video"}, project_id=pid)
    run_all()
    q = parse_qs(urlparse(str(fake.requests[0].url)).query)
    assert "duration" not in q and q["aspectRatio"] == ["9:16"] and q["model"] == ["some/new-video-model"]


def test_image_requests_never_carry_video_or_resolution_parameters(client, make_user, monkeypatch):
    fake = FakePollinations()
    use_pol(monkeypatch, fake)
    h, _ = make_user()
    pid = make_project(client, h)
    generate(client, h, "image", prompt="A castle", options={"aspect_ratio": "16:9"}, project_id=pid)
    run_all()
    q = parse_qs(urlparse(str(fake.requests[0].url)).query)
    assert set(q) == {"width", "height"}                                                                      # no resolution, duration, aspectRatio, audio
    assert KEY_VID not in str(fake.requests[0].url) and fake.requests[0].headers["authorization"] == "Bearer sk_img_SECRET_123456"
    monkeypatch.setattr(get_settings(), "pollinations_image_model", "tongyi-mai/z-image-turbo")
    generate(client, h, "image", prompt="A castle again", options={"aspect_ratio": "1:1"}, project_id=pid)
    run_all()
    q = parse_qs(urlparse(str(fake.requests[-1].url)).query)
    assert "resolution" not in q and q["model"] == ["tongyi-mai/z-image-turbo"]


def test_a_parameter_the_model_rejects_is_dropped_and_the_request_repeated_once(client, make_user, monkeypatch):
    class Picky(FakePollinations):
        def handler(self, request):
            self.requests.append(request)
            if "aspectRatio" in str(request.url):
                return httpx.Response(400, json={"error": "Invalid parameters: tongyi-mai/z-image-turbo does not accept a aspectRatio parameter."})
            return httpx.Response(200, content=__import__("tests.helpers", fromlist=["mp4_bytes"]).mp4_bytes(2), headers={"content-type": "video/mp4"})
    fake = Picky()
    use_pol(monkeypatch, fake)
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "video", prompt="A knight rides", options={"duration_seconds": 10, "aspect_ratio": "16:9", "method": "Text to Video"}, project_id=pid).json()["job_id"]
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "COMPLETED" and len(fake.requests) == 2 and "aspectRatio" not in str(fake.requests[1].url)
    assert asset_info(client, h, j["assets"][0]["id"])["meta"]["dropped_parameters"] == ["aspectRatio"]
    assert used(client, h, "video") == 1


def test_insufficient_balance_and_malformed_answers_are_reported_clearly(client, make_user, monkeypatch):
    h, _ = make_user()
    pid = make_project(client, h)
    use_pol(monkeypatch, FakePollinations(video_status=402))
    jid = generate(client, h, "video", prompt="A knight rides", options={"duration_seconds": 10, "aspect_ratio": "16:9", "method": "Text to Video"}, project_id=pid).json()["job_id"]
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and "quota" in j["error_message"].lower() and used(client, h, "video") == 0
    use_pol(monkeypatch, FakePollinations(video_bytes=b"<html>not a video</html>"))
    jid = generate(client, h, "video", prompt="A knight rides on", options={"duration_seconds": 10, "aspect_ratio": "16:9", "method": "Text to Video"}, project_id=pid).json()["job_id"]
    for _ in range(3):
        run_all()
    assert "can't be played" in job(client, h, jid)["error_message"]


# ------------------------------------------------------------------ music (Hugging Face): exact length with FFmpeg
@pytest.mark.parametrize("asked,generated", [(5, 3.0), (10, 4.0), (15, 22.0)])
def test_music_is_padded_or_trimmed_to_exactly_the_requested_length(client, make_user, monkeypatch, asked, generated):
    wav = tone(".wav", generated)
    use_music(monkeypatch, lambda r: httpx.Response(200, content=wav, headers={"content-type": "audio/wav"}))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "music", prompt="calm strings", options={"duration_seconds": asked}, project_id=pid).json()["job_id"]
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "COMPLETED"
    a = asset_info(client, h, j["assets"][0]["id"])
    assert abs(a["duration_seconds"] - asked) <= 0.35 and a["mime_type"] == "audio/mpeg"
    data = client.get(j["assets"][0]["url"], headers=h).content
    p = os.path.join(tempfile.mkdtemp(), "m.mp3")
    open(p, "wb").write(data)
    assert abs(media.audio_duration(p) - asked) <= 0.35                                                       # the stored file really has that length


def test_music_provider_failures_are_not_faked_and_the_token_stays_server_side(client, make_user, monkeypatch):
    use_music(monkeypatch, lambda r: httpx.Response(401, json={"error": "bad token"}))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "music", prompt="calm strings", options={"duration_seconds": 10}, project_id=pid).json()["job_id"]
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and "api key" in j["error_message"].lower() and "test-music-key" not in json.dumps(j) and not j["assets"]
    ah, _ = make_user("boss@example.com")
    blob = client.get("/api/admin/providers", headers=ah).text + client.get("/api/generate/schema", headers=h).text
    assert "test-music-key" not in blob and '"credential":"MUSIC_API_KEY"' in blob.replace(" ", "")
    from app.providers import registry
    assert registry.get("huggingface").is_configured() and get_settings().music_provider == "huggingface"


# ------------------------------------------------------------------ voice: requested length without cutting speech
def run_voice(client, h, pid, monkeypatch, audio: bytes, seconds):
    from app.providers import knowlez as knowlez_module
    monkeypatch.setattr(get_settings(), "knowlez_api_key", KEY_TTS)
    monkeypatch.setattr(knowlez_module, "http_client", _client(FakeKnowlez(body=audio).handler))
    jid = generate(client, h, "voice", prompt="Hello there friend", options={"duration_seconds": seconds} if seconds else {}, project_id=pid).json()["job_id"]
    run_all()
    j = job(client, h, jid)
    return j, asset_info(client, h, j["assets"][0]["id"])


def test_short_speech_is_padded_slightly_long_speech_sped_up_and_very_long_speech_is_kept_whole(client, make_user, monkeypatch):
    h, _ = make_user()
    pid = make_project(client, h)
    _, a = run_voice(client, h, pid, monkeypatch, mp3_bytes(2.0), 5)
    assert abs(a["duration_seconds"] - 5) <= 0.35 and any("silence" in n for n in a["meta"]["notes"])
    _, a = run_voice(client, h, pid, monkeypatch, mp3_bytes(11.5), 10)
    assert abs(a["duration_seconds"] - 10) <= 0.5 and any("Sped up" in n for n in a["meta"]["notes"])
    _, a = run_voice(client, h, pid, monkeypatch, mp3_bytes(12.0), 5)
    assert a["duration_seconds"] >= 11.5 and any("kept in full" in n for n in a["meta"]["notes"])              # never cut in the middle
    _, a = run_voice(client, h, pid, monkeypatch, mp3_bytes(2.0), None)
    assert abs(a["duration_seconds"] - 2.0) <= 0.4 and not any("silence" in n for n in a["meta"].get("notes", []))     # no length chosen: natural


def test_audio_fitting_degrades_gracefully_when_ffmpeg_cannot_read_the_file():
    data, ext, mime, seconds, notes = media.fit_audio_duration(b"not audio", ".mp3", 5.0)
    assert data == b"not audio" and ext == ".mp3" and notes and seconds is None
