"""Knowlez text-to-speech (POST {base}/v1/tts/synthesise, header X-API-Key). The key is read from KNOWLEZ_API_KEY on the server only.
Documented request: {text (max 4000 chars), voice, format: mp3|pcm, speed: 0.5-2.0, return: audio|url}; the answer is the audio itself (201)."""
import logging
import os
import re
import tempfile

import httpx

from ..config import Settings, get_settings
from .base import ErrorCode, GenerationRequest, ProviderError, ProviderResult
from .http_util import http_client, network_error, raise_for_provider_status
from .voice import EMOTION_PROSODY, VoiceProvider

log = logging.getLogger("dreamcast.knowlez")
MAX_TEXT = 4000
LOCALES = ["en-US", "en-GB", "en-IN"]


class KnowlezVoiceProvider(VoiceProvider):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return "knowlez-voice"

    def validate_config(self) -> list[str]:
        s = self.s
        wanted = s.voice_provider == "knowlez" or (s.voice_provider == "google" and not s.voice_api_key and bool(s.knowlez_api_key))
        if not s.knowlez_api_key:
            return ["KNOWLEZ_API_KEY is not set"]
        return [] if wanted else ["VOICE_PROVIDER is not knowlez"]

    def is_configured(self) -> bool:
        return not self.validate_config()

    def supported_locales(self) -> list[str]:
        return list(LOCALES)

    def max_text_bytes(self) -> int:
        return MAX_TEXT

    def info(self) -> dict:
        return {"model": "Knowlez TTS", "languages": self.supported_languages(),
                "voice_controls": {"gender": "supported", "accent": "American or British voices", "emotion": "approximated (speaking speed)"},
                "credential": "KNOWLEZ_API_KEY", "key_configured": bool(self.s.knowlez_api_key)}

    def _voice(self, locale: str, gender: str) -> str:
        s, british = self.s, locale == "en-GB"
        if gender == "Male":
            return s.knowlez_voice_british_male if british else s.knowlez_voice_male
        return s.knowlez_voice_british_female if british else s.knowlez_voice_female

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        s, o, text = self.s, request.options, request.refined_prompt.strip()
        notes = self.validate_options("voice", o, text)
        locale, _ = self.resolve_locale(o)
        gender = o.get("gender") or "Female"
        if not o.get("gender"):
            notes.append("No gender chosen; a female voice was used.")
        emotion = o.get("emotion") or "Neutral"
        speed = max(0.5, min(2.0, EMOTION_PROSODY[emotion][0]))
        if emotion != "Neutral":
            notes.append(f"Emotion '{emotion}' is approximated with speaking speed; this provider has no true emotion control.")
        if locale == "en-IN":
            notes.append("This provider has no Indian-English voice; an American English voice was used.")
        named = (o.get("voice") or "").strip()
        if named and not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", named):
            raise ProviderError(ErrorCode.INVALID_REQUEST, "invalid voice name", message="That voice name isn't valid.")
        voice = named or self._voice(locale, gender)
        body = {"text": text[:MAX_TEXT], "voice": voice, "format": "mp3", "speed": speed, "return": "audio"}
        url = f"{s.knowlez_base_url.rstrip('/')}/v1/tts/synthesise"
        try:
            with http_client(s.knowlez_timeout_seconds) as client:
                res = client.post(url, json=body, headers={"X-API-Key": s.knowlez_api_key})
        except httpx.HTTPError as e:
            raise network_error(e, "voice")
        raise_for_provider_status(res, "voice", no_quota_statuses=(402,))
        ctype = res.headers.get("content-type", "").split(";")[0].strip().lower()
        if not res.content or ctype.startswith(("application/json", "text/")):
            raise ProviderError(ErrorCode.GENERATION_FAILED, f"no audio in response ({ctype or 'no content-type'})", transient=True,
                                message="The voice provider didn't return audio. Please try again.")
        audio, duration = res.content, None
        from .. import media
        fd, tmp = tempfile.mkstemp(prefix="dc_tts_", suffix=".mp3")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            duration = media.audio_duration(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if self.target_seconds(o):
            audio, fitted = self.fit_length(audio, o, notes)
            duration = fitted or duration
        log.info("knowlez voice ok (%s bytes)", len(audio))
        return ProviderResult(file=(audio, ".mp3", "audio/mpeg"), language="English", duration_seconds=duration,
                              meta={"voice": voice, "locale": locale, "gender": gender, "emotion": emotion, "speed": speed, "notes": notes, "characters": len(text),
                                    "provider": "knowlez"})
