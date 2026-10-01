"""Shared helpers for provider-backed tests. All external HTTP is mocked; no test spends real API credits."""
import base64
import json

import httpx

from app.config import get_settings
from app.db import SessionLocal
from app.providers import music as music_module
from app.providers import text as text_module
from app.providers import voice as voice_module
from app.services import jobs, runner

STORY = """TITLE: The Hidden Kingdom
LOGLINE: A wandering warrior finds a kingdom that only exists at night.
GENRE: Fantasy
SETTING: A mountain valley wrapped in mist, long after the old wars.
MAIN CHARACTERS:
- Kael: a scarred warrior searching for a place to rest.
STORY OUTLINE
ACT 1: Kael follows a strange light into the valley and finds a sleeping city.
ACT 2: The city's queen asks him to break a curse before sunrise.
ACT 3: Kael faces the cursed guardian and learns the curse feeds on fear.
ENDING: The kingdom wakes; Kael chooses to stay as its protector."""

SCRIPT = """TITLE: The Hidden Kingdom
GENRE: Fantasy
LOGLINE: A warrior finds a kingdom that exists only at night.
CHARACTERS:
- KAEL: scarred warrior
CHARACTER NOTES:
- KAEL: silent, resolute
SCENE LIST:
1. EXT. MOUNTAIN PASS - NIGHT: Kael sees a light.
2. INT. SLEEPING CITY - NIGHT: The queen appears.
3. EXT. TOWER - DAWN: The final confrontation.

SCENE 01
EXT. MOUNTAIN PASS - NIGHT
Environment: Thick mist between black cliffs.
Characters: KAEL enters from the left of the frame.
Action:
Kael climbs, breath steaming, and stops as a pale light blooms ahead.
Camera: Slow tracking shot.
Lighting: Cold moonlight.
Sound: Low wind.
DIALOGUE
KAEL:
"Where is everyone?"
Transition: CUT TO

SCENE 02
INT. SLEEPING CITY - NIGHT
Environment: Silent streets of pale stone with frozen lanterns.
Characters: KAEL walks in; the QUEEN watches from a balcony.
Action:
The lanterns flare one by one as Kael passes beneath them.
Camera: Wide, then close on Kael's face.
Sound: A distant bell.
DIALOGUE
QUEEN:
"You are late, warrior."
Transition: CUT TO

SCENE 03
EXT. TOWER - DAWN
Environment: The tower top, rimmed in first light.
Characters: KAEL and the guardian.
Action:
Kael lowers his blade and walks toward the guardian, unarmed.
Music cue: Rising strings.
Transition: FADE OUT
END"""

LYRICS = """TITLE: Battle Dawn
[VERSE 1]
Iron rain on the mountain road
[CHORUS]
Rise with the morning, carry the fire
[BRIDGE]
Hold the line
[FINAL CHORUS]
Rise with the morning, carry the fire"""

WAV = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVEfmt " + b"\x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00" + b"data\x00\x00\x00\x00"
MP3 = b"\xff\xfb\x90\x04" + b"\x00" * 413


def make_png(w: int = 96, h: int = 96, shade: int = 128) -> bytes:
    """A real, valid PNG of the given size (pure Python) for upload tests; different shade = different bytes."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes([shade, 255 - shade, 90]) * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Recorder:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def _client(handler):
    return lambda timeout: httpx.Client(transport=httpx.MockTransport(handler))


def llm_reply(text: str, status: int = 200):
    def h(request):
        if status != 200:
            return httpx.Response(status, json={"error": "x"})
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    return h


def default_llm_handler(rec: Recorder):
    """Answers refinement calls with a short prompt and generation calls with canned story/script/lyrics."""
    def h(request: httpx.Request):
        rec.requests.append(request)
        system = json.loads(request.content)["messages"][0]["content"]
        if "Use exactly this structure" in system and "SCENE 01" in system:
            text = SCRIPT
        elif "story OUTLINE" in system:
            text = STORY
        elif "songwriter" in system:
            text = LYRICS
        else:
            text = "REFINED: " + json.loads(request.content)["messages"][-1]["content"].splitlines()[1][:120]
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    return h


def use_llm(monkeypatch, handler=None) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(get_settings(), "llm_api_key", "test-llm-key")
    monkeypatch.setattr(text_module, "http_client", _client(handler or default_llm_handler(rec)))
    return rec


def use_music(monkeypatch, handler=None, **settings) -> Recorder:
    rec = Recorder()

    def default(request):
        rec.requests.append(request)
        return httpx.Response(200, content=WAV, headers={"content-type": "audio/wav"})

    def wrapped(request):
        rec.requests.append(request) if handler else None
        return (handler or default)(request)

    monkeypatch.setattr(get_settings(), "music_api_key", "test-music-key")
    for k, v in settings.items():
        monkeypatch.setattr(get_settings(), k, v)
    monkeypatch.setattr(music_module, "http_client", _client(wrapped))
    return rec


def use_voice(monkeypatch, handler=None, **settings) -> Recorder:
    rec = Recorder()

    def default(request):
        return httpx.Response(200, json={"audioContent": base64.b64encode(MP3).decode()})

    def wrapped(request):
        rec.requests.append(request)
        return (handler or default)(request)

    monkeypatch.setattr(get_settings(), "voice_api_key", "test-voice-key")
    for k, v in settings.items():
        monkeypatch.setattr(get_settings(), k, v)
    monkeypatch.setattr(voice_module, "http_client", _client(wrapped))
    return rec


def run_all():
    with SessionLocal() as db:
        while (j := jobs.claim_next(db)):
            runner.run_job(j.id)


def make_project(client, h, title="The Lost Kingdom", genre="Fantasy"):
    return client.post("/api/projects", json={"title": title, "genre": genre, "description": "A forgotten realm."}, headers=h).json()["id"]


def generate(client, h, generator, prompt="A warrior discovers a hidden kingdom.", options=None, project_id=None, refined=None, **kw):
    r = client.post("/api/generations", headers=h, json={"generator_type": generator, "original_prompt": prompt,
                    "refined_prompt": refined if refined is not None else prompt, "options": options or {}, "project_id": project_id, **kw})
    return r


def generate_and_run(client, h, generator, **kw):
    r = generate(client, h, generator, **kw)
    assert r.status_code == 201, r.text
    run_all()
    return client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()


def used(client, h, gen):
    return next(i["used"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == gen)


# ---------------------------------------------------------------- video / face (mocked fal.ai queue API)
_MP4_CACHE: dict[int, bytes] = {}


def mp4_bytes(seconds: int = 2) -> bytes:
    """A real, tiny, decodable MP4 (test pattern made by the bundled ffmpeg). Test fixture only; the app never fabricates video."""
    if seconds not in _MP4_CACHE:
        import os
        import subprocess
        import tempfile

        import imageio_ffmpeg
        path = os.path.join(tempfile.mkdtemp(), "t.mp4")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x180:rate=10",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", path], capture_output=True, check=True)
        _MP4_CACHE[seconds] = open(path, "rb").read()
    return _MP4_CACHE[seconds]


class FakeFal:
    """Stands in for queue.fal.run: submit -> status (scripted sequence) -> result -> file download -> cancel."""
    ORIGIN = "https://queue.fal.run"
    FILES = "https://v3.fal.media"

    def __init__(self, statuses=("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), submit_status=200, result_status=200, cancel_status=202,
                 status_errors=0, result_bytes=None, result_kind="video"):
        self.statuses, self.submit_status, self.result_status = list(statuses), submit_status, result_status
        self.cancel_status, self.status_errors, self.result_kind = cancel_status, status_errors, result_kind
        self.result_bytes = result_bytes
        self.submits: list[dict] = []
        self.status_calls = 0
        self.cancel_calls = 0
        self.headers_seen: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, method = str(request.url), request.method
        self.headers_seen.append(dict(request.headers))
        if method == "POST":
            self.submits.append({"model": url.removeprefix(self.ORIGIN + "/"), "payload": json.loads(request.content)})
            if self.submit_status != 200:
                return httpx.Response(self.submit_status, json={"detail": "nope balance" if self.submit_status == 403 else "x"})
            rid = f"req{len(self.submits)}"
            base = f"{self.ORIGIN}/fal-ai/app/requests/{rid}"
            return httpx.Response(200, json={"request_id": rid, "status_url": base + "/status", "response_url": base, "cancel_url": base + "/cancel"})
        if method == "PUT":
            self.cancel_calls += 1
            return httpx.Response(self.cancel_status, json={"status": "CANCELLATION_REQUESTED"})
        if url.endswith("/status"):
            self.status_calls += 1
            if self.status_errors > 0:
                self.status_errors -= 1
                return httpx.Response(503, json={})
            i = min(self.status_calls - 1, len(self.statuses) - 1)
            return httpx.Response(200, json={"status": self.statuses[i]})
        if url.startswith(self.FILES):
            if self.result_kind == "video":
                return httpx.Response(200, content=self.result_bytes or mp4_bytes(), headers={"content-type": "video/mp4"})
            return httpx.Response(200, content=self.result_bytes or make_png(128, 128, 60), headers={"content-type": "image/png"})
        if "/requests/" in url:                                     # result JSON
            if self.result_status != 200:
                return httpx.Response(self.result_status, json={"detail": "model failed"})
            key = "video" if self.result_kind == "video" else "image"
            return httpx.Response(200, json={key: {"url": f"{self.FILES}/out/{key}"}})
        return httpx.Response(404)


def use_fal(monkeypatch, fake: FakeFal, video=True, face=False, **settings) -> FakeFal:
    from app.providers import fal as fal_module
    s = get_settings()
    if video:
        monkeypatch.setattr(s, "video_provider_api_key", "test-video-key")
    if face:
        monkeypatch.setattr(s, "face_provider_api_key", "test-face-key")
    for k, v in settings.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(s, "video_poll_seconds", 0.0)
    monkeypatch.setattr(s, "face_poll_seconds", 0.0)
    monkeypatch.setattr(s, "image_poll_seconds", 0.0)
    monkeypatch.setattr(fal_module, "http_client", _client(fake.handler))
    return fake


def upload_ref(client, h, pid, data, name="a.png", type_="OTHER", mime="image/png"):
    r = client.post(f"/api/projects/{pid}/references", headers=h, data={"type": type_}, files={"file": (name, data, mime)})
    assert r.status_code in (200, 201), r.text
    return r.json()
