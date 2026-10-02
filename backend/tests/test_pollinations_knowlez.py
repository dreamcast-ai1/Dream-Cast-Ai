"""Pollinations (image/video) and Knowlez (TTS) behind the provider interfaces, plus scene narration and the final FFmpeg render.
All provider HTTP is mocked (a fake that answers like the documented APIs); no real call and no credits are used."""
import json
import os
import subprocess
import tempfile
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import imageio_ffmpeg
import pytest

from app import media
from app.config import get_settings
from app.db import SessionLocal
from app.models import GeneratedAsset, GenerationJob
from app.providers import knowlez as knowlez_module
from app.providers import pollinations as pol_module

from .helpers import _client, generate, make_png, make_project, mp4_bytes, run_all

pytestmark = pytest.mark.usefixtures("all_features", "advanced_options")
KEY_IMG, KEY_VID, KEY_TTS = "sk_img_SECRET_123456", "sk_vid_SECRET_123456", "knz_SECRET_123456"


def mp3_bytes(seconds: float = 1.5) -> bytes:
    path = os.path.join(tempfile.mkdtemp(), "t.mp3")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-codec:a", "libmp3lame", path], capture_output=True, check=True)
    return open(path, "rb").read()


class FakePollinations:
    def __init__(self, image_status=200, video_status=200, image_type="image/png", video_bytes=None):
        self.requests: list[httpx.Request] = []
        self.image_status, self.video_status, self.image_type = image_status, video_status, image_type
        self.video_bytes = video_bytes

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = urlparse(str(request.url)).path
        if path.startswith("/image/"):
            if self.image_status != 200:
                return httpx.Response(self.image_status, json={"error": "nope"}, headers={"retry-after": "1"})
            body = make_png(64, 64, 7) if self.image_type.startswith("image/") else b'{"oops": true}'
            return httpx.Response(200, content=body, headers={"content-type": self.image_type})
        if path.startswith("/video/"):
            if self.video_status != 200:
                return httpx.Response(self.video_status, json={"error": "nope"})
            return httpx.Response(200, content=self.video_bytes or mp4_bytes(2), headers={"content-type": "video/mp4"})
        return httpx.Response(404)


class FakeKnowlez:
    def __init__(self, status=200, body=None, ctype="audio/mpeg"):
        self.requests: list[httpx.Request] = []
        self.status, self.body, self.ctype = status, body, ctype

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200 and self.status != 201:
            return httpx.Response(self.status, json={"error": "nope"})
        return httpx.Response(201, content=self.body if self.body is not None else mp3_bytes(1.5), headers={"content-type": self.ctype})


@pytest.fixture
def pol(monkeypatch):
    def use(fake: FakePollinations | None = None, image=True, video=True):
        fake = fake or FakePollinations()
        s = get_settings()
        if image:
            monkeypatch.setattr(s, "pollinations_image_api_key", KEY_IMG)
        if video:
            monkeypatch.setattr(s, "pollinations_video_api_key", KEY_VID)
        monkeypatch.setattr(pol_module, "http_client", _client(fake.handler))
        return fake
    return use


@pytest.fixture
def knz(monkeypatch):
    def use(fake: FakeKnowlez | None = None):
        fake = fake or FakeKnowlez()
        monkeypatch.setattr(get_settings(), "knowlez_api_key", KEY_TTS)
        monkeypatch.setattr(knowlez_module, "http_client", _client(fake.handler))
        return fake
    return use


def job(client, h, jid):
    return client.get(f"/api/jobs/{jid}", headers=h).json()


def asset_info(client, h, aid):
    return client.get(f"/api/assets/{aid}", headers=h).json()


def used(client, h, gen):
    return next(i["used"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == gen)


# ------------------------------------------------------------------ provider selection
def test_pollinations_is_used_when_only_its_key_is_set_and_fal_wins_when_fal_has_a_key(client, make_user, pol, monkeypatch):
    from app.providers import registry
    pol()
    assert registry.get("pollinations-image").is_configured() and registry.get("pollinations-video").is_configured()
    assert not registry.get("fal-image").is_configured() and not registry.get("fal-video").is_configured()
    s = get_settings()
    monkeypatch.setattr(s, "video_provider_api_key", "fal-key")                     # fal has a key and is the default: it keeps priority
    assert registry.get("fal-video").is_configured() and not registry.get("pollinations-video").is_configured()
    monkeypatch.setattr(s, "video_provider", "pollinations")                         # ...unless Pollinations is chosen explicitly
    assert registry.get("pollinations-video").is_configured() and not registry.get("fal-video").is_configured()


def test_knowlez_is_used_when_only_its_key_is_set(knz, monkeypatch):
    from app.providers import registry
    knz()
    assert registry.get("knowlez-voice").is_configured() and not registry.get("google").is_configured()
    monkeypatch.setattr(get_settings(), "voice_api_key", "google-key")
    assert registry.get("google").is_configured() and not registry.get("knowlez-voice").is_configured()


def test_the_admin_and_user_apis_never_return_the_new_keys(client, make_user, pol, knz):
    pol(); knz()
    ah, _ = make_user("boss@example.com")
    blob = "".join(client.get(u, headers=ah).text for u in ("/api/admin/providers", "/api/settings/providers", "/api/generate/schema", "/api/admin/system", "/api/features"))
    for k in (KEY_IMG, KEY_VID, KEY_TTS):
        assert k not in blob


# ------------------------------------------------------------------ image
def test_image_is_generated_through_pollinations_stored_and_shown(client, make_user, pol):
    fake = pol()
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, "image", prompt="A lone warrior at a ruined gate, sunrise", options={"aspect_ratio": "16:9"}, project_id=pid)
    assert r.status_code == 201, r.text
    run_all()
    j = job(client, h, r.json()["job_id"])
    assert j["status"] == "COMPLETED" and j["provider"] == "pollinations-image" and j["assets"][0]["url"]
    req = fake.requests[0]
    assert req.method == "GET" and req.headers["authorization"] == f"Bearer {KEY_IMG}"
    assert unquote(urlparse(str(req.url)).path) == "/image/A lone warrior at a ruined gate, sunrise"
    q = parse_qs(urlparse(str(req.url)).query)
    assert q["width"] == ["1280"] and q["height"] == ["720"] and "key" not in q and KEY_IMG not in str(req.url)
    a = asset_info(client, h, j["assets"][0]["id"])
    assert a["type"] == "IMAGE" and a["mime_type"] == "image/png" and client.get(j["assets"][0]["url"], headers=h).content.startswith(b"\x89PNG")
    assert used(client, h, "image") == 1
    other, _ = make_user("o@example.com")
    assert client.get(j["assets"][0]["url"], headers=other).status_code == 404


@pytest.mark.parametrize("status,refund", [(401, True), (402, True), (429, True), (400, True)])
def test_image_provider_errors_become_clean_failures_and_refund(client, make_user, pol, status, refund):
    pol(FakePollinations(image_status=status))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "image", prompt="A castle at dawn", options={"aspect_ratio": "1:1"}, project_id=pid).json()["job_id"]
    for _ in range(2):
        run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and j["error_message"] and KEY_IMG not in json.dumps(j) and "Traceback" not in j["error_message"]
    assert used(client, h, "image") == 0


def test_image_5xx_is_retried_once_then_reported(client, make_user, pol):
    fake = pol(FakePollinations(image_status=502))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "image", prompt="A castle at dawn", options={"aspect_ratio": "1:1"}, project_id=pid).json()["job_id"]
    for _ in range(3):
        run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and "unavailable" in j["error_message"].lower() and len(fake.requests) == 2


def test_a_non_image_answer_and_a_timeout_do_not_crash_the_worker(client, make_user, pol, monkeypatch):
    pol(FakePollinations(image_type="application/json"))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "image", prompt="A castle at dawn", options={"aspect_ratio": "1:1"}, project_id=pid).json()["job_id"]
    for _ in range(3):
        run_all()
    assert "didn't return an image" in job(client, h, jid)["error_message"]

    def boom(request):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(pol_module, "http_client", _client(boom))
    jid2 = generate(client, h, "image", prompt="Another castle", options={"aspect_ratio": "1:1"}, project_id=pid).json()["job_id"]
    for _ in range(3):
        run_all()
    j = job(client, h, jid2)
    assert j["status"] == "FAILED" and "timed out" in j["error_message"]


# ------------------------------------------------------------------ video
def test_video_is_generated_through_pollinations_with_its_real_length(client, make_user, pol):
    fake = pol()
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, "video", prompt="A knight rides across a misty field", options={"duration_seconds": 10, "aspect_ratio": "16:9", "method": "Text to Video"}, project_id=pid)
    assert r.status_code == 201, r.text
    run_all()
    j = job(client, h, r.json()["job_id"])
    assert j["status"] == "COMPLETED" and j["provider"] == "pollinations-video"
    req = fake.requests[0]
    assert urlparse(str(req.url)).path.startswith("/video/") and req.headers["authorization"] == f"Bearer {KEY_VID}"
    assert parse_qs(urlparse(str(req.url)).query)["aspectRatio"] == ["16:9"]
    a = asset_info(client, h, j["assets"][0]["id"])
    assert a["type"] == "VIDEO" and a["mime_type"] == "video/mp4" and 1.5 <= a["duration_seconds"] <= 2.5       # the clip really is ~2 s, whatever the form asked for
    s = client.post("/api/media/stream-url", headers=h, json={"kind": "asset", "id": a["id"]})
    assert client.get(s.json()["url"], headers={"Range": "bytes=0-9"}).status_code in (200, 206)
    assert used(client, h, "video") == 1


def test_video_failures_refund_and_an_undecodable_file_is_rejected(client, make_user, pol):
    h, _ = make_user()
    pid = make_project(client, h)
    opts = {"duration_seconds": 10, "aspect_ratio": "16:9", "method": "Text to Video"}
    pol(FakePollinations(video_status=402))
    jid = generate(client, h, "video", prompt="A knight rides", options=opts, project_id=pid).json()["job_id"]
    for _ in range(2):
        run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and "quota" in j["error_message"].lower() and used(client, h, "video") == 0
    pol(FakePollinations(video_bytes=b"this is not a video"))
    jid = generate(client, h, "video", prompt="A knight rides again", options=opts, project_id=pid).json()["job_id"]
    for _ in range(3):
        run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and "can't be played" in j["error_message"]


def test_image_to_video_is_honestly_unsupported_on_pollinations(client, make_user, pol):
    pol()
    h, _ = make_user()
    pid = make_project(client, h)
    from .helpers import upload_ref
    ref = upload_ref(client, h, pid, make_png(80, 80, 3))
    r = generate(client, h, "video", prompt="x y z", options={"method": "Image to Video", "duration_seconds": 10}, project_id=pid, reference_assets=[ref["id"]])
    assert r.status_code == 422 and "image-to-video" in r.json()["error"]["message"].lower()


# ------------------------------------------------------------------ voice (Knowlez)
def test_voice_goes_to_knowlez_with_the_documented_request_and_is_stored_with_its_duration(client, make_user, knz):
    fake = knz()
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, "voice", prompt="The kingdom will rise again.", options={"gender": "Male", "emotion": "Excited", "accent": "American"}, project_id=pid)
    assert r.status_code == 201, r.text
    run_all()
    j = job(client, h, r.json()["job_id"])
    assert j["status"] == "COMPLETED" and j["provider"] == "knowlez-voice"
    req = fake.requests[0]
    assert req.method == "POST" and str(req.url) == "https://api-tts.knowlez.com/v1/tts/synthesise" and req.headers["x-api-key"] == KEY_TTS
    body = json.loads(req.content)
    assert body["text"] == "The kingdom will rise again." and body["voice"] == "am_adam" and body["format"] == "mp3" and body["return"] == "audio" and body["speed"] > 1.0
    assert KEY_TTS not in req.content.decode()
    a = asset_info(client, h, j["assets"][0]["id"])
    assert a["type"] == "VOICE" and a["mime_type"] == "audio/mpeg" and 1.0 <= a["duration_seconds"] <= 2.0
    assert len(client.get(j["assets"][0]["url"], headers=h).content) > 500


def test_voice_maps_gender_and_british_accent_to_the_configured_voices(client, make_user, knz):
    fake = knz()
    h, _ = make_user()
    pid = make_project(client, h)
    for gender, accent, voice in (("Female", "American", "af_bella"), ("Female", "British", "bf_emma"), ("Male", "British", "bm_george")):
        generate(client, h, "voice", prompt="Hello there friend", options={"gender": gender, "accent": accent}, project_id=pid)
    run_all()
    assert [json.loads(r.content)["voice"] for r in fake.requests] == ["af_bella", "bf_emma", "bm_george"]


def test_knowlez_failures_and_unsupported_languages_are_explained(client, make_user, knz):
    h, _ = make_user()
    pid = make_project(client, h)
    for fake, fragment in ((FakeKnowlez(status=401), "rejected its api key"), (FakeKnowlez(status=402), "quota"), (FakeKnowlez(body=b'{"url":"x"}', ctype="application/json"), "didn't return audio")):
        knz(fake)
        jid = generate(client, h, "voice", prompt="Hello there friend", options={"gender": "Female"}, project_id=pid).json()["job_id"]
        for _ in range(3):
            run_all()
        j = job(client, h, jid)
        assert j["status"] == "FAILED" and fragment in j["error_message"].lower() and KEY_TTS not in json.dumps(j)
    assert used(client, h, "voice") == 0           # nothing usable was produced by the provider in any of the three cases: all refunded
    knz()
    jid = generate(client, h, "voice", prompt="Namaste", options={"language": "Hindi"}, project_id=pid)
    assert jid.status_code == 422 and "hindi" in jid.json()["error"]["message"].lower()


# ------------------------------------------------------------------ narration + final render
def _ffprobe(path):
    return media.probe(path), media.audio_duration(path)


def test_scene_narration_and_music_are_rendered_into_a_playable_mp4(client, make_user, pol, knz, monkeypatch):
    pol(); knz(FakeKnowlez(body=mp3_bytes(3.0)))                       # a 3 s narration over a 2 s clip: the last frame is held
    from app.providers import music as music_module
    monkeypatch.setattr(get_settings(), "music_api_key", "hf-key")
    wav = os.path.join(tempfile.mkdtemp(), "m.wav")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=2", wav], capture_output=True, check=True)
    monkeypatch.setattr(music_module, "http_client", _client(lambda r: httpx.Response(200, content=open(wav, "rb").read(), headers={"content-type": "audio/wav"})))
    h, _ = make_user()
    pid = make_project(client, h)
    scenes = []
    for i in range(2):
        s = client.post(f"/api/projects/{pid}/scenes", headers=h, json={"title": f"S{i}", "description": f"A knight rides, part {i}.", "script": f"The knight whispers part {i}."}).json()
        scenes.append(s)
        assert client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-video", headers=h).status_code == 201
    run_all()
    nar = client.post(f"/api/projects/{pid}/scenes/{scenes[0]['id']}/generate-narration", headers=h, json={"gender": "Female"})
    assert nar.status_code == 201, nar.text
    assert client.post(f"/api/projects/{pid}/scenes/{scenes[0]['id']}/generate-narration", headers=h).status_code == 409        # no duplicate while running
    run_all()
    state = client.get(f"/api/projects/{pid}/scenes", headers=h).json()["items"]
    assert state[0]["narration"]["asset_id"] and state[0]["narration"]["duration_seconds"] and state[1]["narration"] is None
    mj = generate(client, h, "music", prompt="calm strings", options={"duration_seconds": 10}, project_id=pid).json()["job_id"]
    run_all()
    music_id = job(client, h, mj)["assets"][0]["id"]
    r = client.post(f"/api/projects/{pid}/movie/assemble", headers=h, json={"narration": True, "music_asset_id": music_id})
    assert r.status_code == 202, r.text
    run_all()
    mj = job(client, h, r.json()["job_id"])
    assert mj["status"] == "COMPLETED", mj
    movie = client.get(f"/api/projects/{pid}/movie", headers=h).json()["movie"]
    assert movie and movie["format"] == "mp4" and movie["mime_type"] == "video/mp4"
    with SessionLocal() as db:
        a = db.get(GeneratedAsset, movie["id"])
        from app.storage import get_storage
        path = str(get_storage().local_path(a.file_path))
    info = media.probe(path)
    assert info and info.width and info.has_audio                                  # a real, decodable MP4 with a soundtrack
    assert 4.5 <= info.duration <= 6.5                                             # scene 1: 3 s (held frame), scene 2: 2 s
    head = open(path, "rb").read(12)
    assert head[4:8] == b"ftyp"
    assert "h264" in subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr         # browser-playable codec
    assert client.get(f"/api/files/asset/{movie['id']}", headers=h).status_code == 200
    other, _ = make_user("o@example.com")
    assert client.get(f"/api/files/asset/{movie['id']}", headers=other).status_code == 404
    # someone else's music cannot be mixed into my movie
    oh, _ = make_user("o2@example.com")
    assert client.post(f"/api/projects/{pid}/movie/assemble", headers=oh, json={"music_asset_id": music_id}).status_code == 404


def test_narration_needs_text_and_respects_ownership(client, make_user, knz):
    knz()
    h, _ = make_user()
    other, _ = make_user("o@example.com")
    pid = make_project(client, h)
    s = client.post(f"/api/projects/{pid}/scenes", headers=h, json={"title": "", "description": "", "script": ""}).json()
    assert client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-narration", headers=h).status_code == 422
    assert client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-narration", headers=other).status_code == 404


def test_assembly_without_narration_or_music_still_works_as_before(client, make_user, pol):
    pol()
    h, _ = make_user()
    pid = make_project(client, h)
    s = client.post(f"/api/projects/{pid}/scenes", headers=h, json={"title": "S", "description": "A knight rides."}).json()
    client.post(f"/api/projects/{pid}/scenes/{s['id']}/generate-video", headers=h)
    run_all()
    r = client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    assert r.status_code == 202
    run_all()
    assert job(client, h, r.json()["job_id"])["status"] == "COMPLETED"
