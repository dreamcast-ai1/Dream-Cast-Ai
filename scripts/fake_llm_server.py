#!/usr/bin/env python3
"""DEV/TEST ONLY: local stand-ins for the external AI services, so the whole DreamCast pipeline can be exercised without
API keys or credits. They do NOT call any AI: text is assembled from the request, audio is a generated tone or silence.

  backend/.venv/bin/python scripts/fake_llm_server.py    # LLM :9099, music :9098, voice :9097, fal.ai-style video/face queue :9096
  (use the backend venv's python: the video/face stand-in needs imageio-ffmpeg to make a real test-pattern MP4/PNG)

Point .env at them:
  LLM_PROVIDER=custom  LLM_BASE_URL=http://127.0.0.1:9099/v1  LLM_MODEL=fake  LLM_API_KEY=dev
  MUSIC_API_KEY=dev    MUSIC_BASE_URL=http://127.0.0.1:9098/models
  VOICE_API_KEY=dev    VOICE_BASE_URL=http://127.0.0.1:9097
  VIDEO_PROVIDER_API_KEY=dev  VIDEO_PROVIDER_BASE_URL=http://127.0.0.1:9096  VIDEO_MAX_SECONDS=30   (FACE_PROVIDER_API_KEY / FACE_PROVIDER_BASE_URL the same way)
"""
import base64
import io
import json
import math
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "127.0.0.1"


def _story(req: str) -> str:
    return f"""TITLE: The Hidden Kingdom
LOGLINE: {req[:110].strip() or 'A traveller finds a place that should not exist.'}
GENRE: Fantasy
SETTING: A mist-covered valley where a city appears only after dark.
MAIN CHARACTERS:
- Kael: a scarred warrior who has stopped believing in safe places.
- Queen Mira: ruler of the night city, bound by an old curse.
STORY OUTLINE
ACT 1: Kael follows a faint light through the mountains and finds a sleeping city that wakes as he enters.
ACT 2: Queen Mira asks him to break the curse before sunrise. Every solution costs him something he values.
ACT 3: Kael faces the cursed guardian and realises the curse feeds on fear, not on force.
ENDING: The curse breaks at dawn; the kingdom becomes real and Kael chooses to stay as its protector."""


def _script(req: str, story: str) -> str:
    scenes = [("EXT. MOUNTAIN PASS - NIGHT", "Kael climbs through mist toward a pale light."),
              ("INT. SLEEPING CITY - NIGHT", "Lanterns ignite one by one as Kael walks beneath them."),
              ("EXT. TOWER - DAWN", "Kael lowers his blade and approaches the guardian unarmed.")]
    out = ["TITLE: The Hidden Kingdom", "GENRE: Fantasy", "LOGLINE: A warrior finds a kingdom that exists only at night.", "CHARACTERS:",
           "- KAEL: scarred warrior", "- QUEEN MIRA: ruler of the night city", "CHARACTER NOTES:", "- KAEL: silent, resolute, learning to trust",
           "SCENE LIST:"]
    out += [f"{i}. {h}: {a}" for i, (h, a) in enumerate(scenes, 1)]
    for i, (h, a) in enumerate(scenes, 1):
        out += ["", f"SCENE {i:02d}", h, "Environment: " + a, "Characters: KAEL, QUEEN MIRA", "Action:", a + " The air is still and cold.",
                "Camera: Slow tracking shot.", "Lighting: Cold moonlight with warm lantern glow.", "Sound: Low wind, distant bell.", "DIALOGUE",
                "KAEL:", '"Where is everyone?"', "QUEEN MIRA:", '"You are late, warrior."', "Transition: CUT TO"]
    out.append("END")
    if story:
        out.insert(3, "(adapted from the supplied story)")
    return "\n".join(out)


def _lyrics(req: str) -> str:
    return """TITLE: Carry the Fire
[VERSE 1]
Iron rain on the mountain road
Every step a promise that I owe
[CHORUS]
Rise with the morning, carry the fire
Hold the line, we're going higher
[BRIDGE]
When the night is heavy, we stay
[FINAL CHORUS]
Rise with the morning, carry the fire"""


class LLM(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
        auth = self.headers.get("Authorization", "")
        kind = "story" if "story OUTLINE" in system else "script" if "SCENE 01" in system else "lyrics" if "songwriter" in system else "refine"
        print(f"[fake-llm] {kind} model={body.get('model')} auth={'Bearer ***' if auth.startswith('Bearer ') else 'MISSING'} max_tokens={body.get('max_tokens')}", flush=True)
        if not auth.startswith("Bearer "):
            self.send_response(401); self.end_headers(); return
        req = next((l for l in user.splitlines() if l.startswith("User request:")), user).removeprefix("User request:").strip()
        text = {"story": _story(req), "script": _script(req, "STORY TO ADAPT" in user), "lyrics": _lyrics(req)}.get(kind) or \
            f"[FAKE LLM] {req} Wide establishing shot, slow tracking camera, moody torch lighting."
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass


class Music(BaseHTTPRequestHandler):
    """Hugging Face-style text-to-audio: POST /models/<model> -> audio bytes. Produces a real, audible WAV tone sweep."""
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.headers.get("Authorization", "") != "Bearer dev":
            self.send_response(401); self.end_headers(); return
        seconds = max(1, int(body.get("parameters", {}).get("max_new_tokens", 500)) // 50)
        print(f"[fake-music] {seconds}s prompt={body.get('inputs', '')[:80]!r}", flush=True)
        rate, buf = 16000, io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * (220 + 110 * (i / rate % 4)) * i / rate))) for i in range(rate * seconds)))
        data = buf.getvalue()
        self.send_response(200); self.send_header("Content-Type", "audio/wav"); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a): pass


class Voice(BaseHTTPRequestHandler):
    """Google TTS-style: POST /v1/text:synthesize -> {audioContent: base64 mp3}. Returns silent (but valid) MP3 frames sized to the text."""
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.headers.get("X-Goog-Api-Key") != "dev":
            self.send_response(403); self.end_headers(); self.wfile.write(b'{"error":{"message":"API key not valid"}}'); return
        text = body.get("input", {}).get("text", "")
        print(f"[fake-voice] voice={body.get('voice')} audio={body.get('audioConfig')} chars={len(text)}", flush=True)
        frames = max(40, int(len(text) / 15 / 0.026))          # ~15 chars/s of speech, 26 ms per MPEG-1 Layer III frame
        mp3 = (b"\xff\xfb\x90\x64" + b"\x00" * 413) * frames
        out = json.dumps({"audioContent": base64.b64encode(mp3).decode()}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass


class Fal(BaseHTTPRequestHandler):
    """fal.ai queue-style API: POST /<model> -> {request_id,status_url,response_url,cancel_url}; status advances with time
    (IN_QUEUE -> IN_PROGRESS -> COMPLETED); result JSON points at a file this server serves. The MP4/PNG are real files (ffmpeg
    test patterns): this is a stand-in for exercising the pipeline, NOT a generator."""
    jobs: dict = {}
    SECONDS = 6.0

    def _json(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)

    def _auth(self):
        if self.headers.get("Authorization") != "Key dev":
            self._json(401, {"detail": "unauthorized"})
            return False
        return True

    def _origin(self):
        return f"http://{self.headers.get('Host')}"

    def do_POST(self):
        import time
        if not self._auth():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        rid = f"fake{len(self.jobs) + 1}"
        model = self.path.lstrip("/")
        self.jobs[rid] = {"t": time.monotonic(), "model": model, "payload": body}
        keys = {k: (v if not str(v).startswith("data:") else f"<data-uri {len(v)} chars>") for k, v in body.items()}
        print(f"[fake-fal] submit {rid} model={model} input={keys}", flush=True)
        base = f"{self._origin()}/req/{rid}"
        self._json(200, {"request_id": rid, "status_url": base + "/status", "response_url": base, "cancel_url": base + "/cancel"})

    def do_PUT(self):
        import time
        if not self._auth():
            return
        rid = self.path.split("/")[2]
        job = self.jobs.get(rid)
        ok = job is not None and time.monotonic() - job["t"] < self.SECONDS / 3
        if ok:
            job["cancelled"] = True
        print(f"[fake-fal] cancel {rid} -> {'accepted' if ok else 'refused (already running)'}", flush=True)
        self._json(202 if ok else 400, {"status": "CANCELLATION_REQUESTED" if ok else "ALREADY_IN_PROGRESS"})

    def do_GET(self):
        import time
        parts = self.path.split("/")
        if parts[1] == "files":                                   # unauthenticated download URL, like a CDN link
            rid, ext = parts[2].rsplit(".", 1)
            data = self._media(rid, ext)
            self.send_response(200); self.send_header("Content-Type", "video/mp4" if ext == "mp4" else "image/png")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            return
        if not self._auth():
            return
        rid, what = parts[2], (parts[3] if len(parts) > 3 else "")
        job = self.jobs.get(rid)
        if not job:
            return self._json(404, {"detail": "not found"})
        elapsed = time.monotonic() - job["t"]
        if what == "status":
            state = "IN_QUEUE" if elapsed < self.SECONDS / 3 else "IN_PROGRESS" if elapsed < self.SECONDS else "COMPLETED"
            print(f"[fake-fal] status {rid} {state}", flush=True)
            return self._json(200, {"status": state})
        if "face" in job["model"]:
            return self._json(200, {"image": {"url": f"{self._origin()}/files/{rid}.png"}})
        return self._json(200, {"video": {"url": f"{self._origin()}/files/{rid}.mp4"}})

    def _media(self, rid, ext):
        import os
        import subprocess
        import tempfile

        import imageio_ffmpeg
        job = self.jobs[rid]
        seconds = int(job["payload"].get("duration", 10))
        w, h = (180, 320) if job["payload"].get("aspect_ratio") == "9:16" else (320, 180)
        out = os.path.join(tempfile.mkdtemp(), f"o.{ext}")
        src = f"testsrc=duration={seconds}:size={w}x{h}:rate=10"
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", src, "-pix_fmt", "yuv420p", "-c:v", "libx264", out] if ext == "mp4" else \
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", "testsrc=size=256x256:rate=1", "-frames:v", "1", out]
        subprocess.run(cmd, capture_output=True, check=True)
        return open(out, "rb").read()

    def log_message(self, *a): pass


if __name__ == "__main__":
    threading.Thread(target=HTTPServer((HOST, 9096), Fal).serve_forever, daemon=True).start()
    for port, handler in ((9098, Music), (9097, Voice)):
        threading.Thread(target=HTTPServer((HOST, port), handler).serve_forever, daemon=True).start()
    print(f"fake LLM :9099  music :9098  voice :9097  video/face queue :9096 on {HOST} (dev only)", flush=True)
    HTTPServer((HOST, 9099), LLM).serve_forever()
