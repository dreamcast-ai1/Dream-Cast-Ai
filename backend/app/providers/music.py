"""Music generation providers. Each subclass hides one vendor; the rest of the app only sees MusicProvider."""
import re
from abc import abstractmethod

import httpx

from ..config import Settings, get_settings
from .base import ErrorCode, GenerationRequest, ProviderCapability, ProviderError, ProviderResult, SyncProvider
from .http_util import http_client, network_error, raise_for_provider_status

DURATIONS = (5, 10, 15)
AUDIO_EXT = {"audio/flac": (".flac", "audio/flac"), "audio/x-flac": (".flac", "audio/flac"), "audio/wav": (".wav", "audio/wav"),
             "audio/x-wav": (".wav", "audio/wav"), "audio/wave": (".wav", "audio/wav"), "audio/mpeg": (".mp3", "audio/mpeg"),
             "audio/mp3": (".mp3", "audio/mpeg"), "audio/ogg": (".ogg", "audio/ogg")}


class MusicProvider(SyncProvider):
    capability = ProviderCapability.MUSIC
    generators = frozenset({"music"})
    label = "Music"
    supports_lyrics = False       # whether lyrics can condition the audio (sung vocals). MusicGen: no.
    not_configured_message = "Music provider is not configured. An administrator needs to set MUSIC_API_KEY."

    @abstractmethod
    def supported_durations(self) -> list[int]: ...

    def adapt_options(self, generator: str, options: dict) -> list[str]:
        allowed = self.supported_durations()
        if options.get("duration_seconds") not in allowed:
            options["duration_seconds"] = max(allowed)
            return [f"The music provider supports clips up to {max(allowed)} seconds. Duration adjusted to {max(allowed)} seconds."]
        return []

    def validate_options(self, generator: str, options: dict, text: str = "", refs: list[dict] | None = None) -> list[str]:
        if options.get("vocals_mode") == "Instrumental + Vocals" and not self.supports_lyrics:
            raise ProviderError(ErrorCode.INVALID_REQUEST, "vocals unsupported",
                                message="The configured music provider can only make instrumental music. Choose Instrumental, or ask an administrator to connect a provider that can sing.")
        d = options.get("duration_seconds")
        allowed = self.supported_durations()
        if d is not None and int(d) not in allowed:
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"duration {d} unsupported",
                                message=f"The configured music provider supports clips of {', '.join(map(str, allowed))} seconds "
                                        f"(maximum {max(allowed)}). Please choose a supported duration.")
        return []


class HuggingFaceMusicProvider(MusicProvider):
    """MusicGen (or any text-to-audio model) via the Hugging Face Inference API. Free tokens exist; free models can be slow to load."""
    TOKENS_PER_SECOND = 50   # MusicGen decodes ~50 audio tokens per second

    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.s.music_provider

    @property
    def model(self) -> str:
        return self.s.music_model

    def _url(self) -> str:
        base = (self.s.music_base_url or "https://api-inference.huggingface.co/models").rstrip("/")
        return f"{base}/{self.model}"

    def validate_config(self) -> list[str]:
        problems = []
        if self.s.music_provider != "huggingface":
            problems.append("MUSIC_PROVIDER must be: huggingface")
        if not self.s.music_api_key:
            problems.append("MUSIC_API_KEY is not set")
        if not self.model:
            problems.append("MUSIC_MODEL is not set")
        return problems

    def is_configured(self) -> bool:
        return not self.validate_config()

    def supported_durations(self) -> list[int]:
        return [d for d in DURATIONS if d <= self.s.music_max_seconds] or [DURATIONS[0]]

    def info(self) -> dict:
        return {"model": self.model, "credential": "MUSIC_API_KEY", "durations": self.supported_durations(), "supports_lyrics": self.supports_lyrics, "supports_vocals": self.supports_lyrics}

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        self.validate_options("music", request.options)
        duration = int(request.options.get("duration_seconds") or 10)
        prompt = re.sub(r"\s+", " ", request.refined_prompt).strip()[:600]
        body = {"inputs": prompt, "parameters": {"max_new_tokens": duration * self.TOKENS_PER_SECOND}}
        headers = {"Authorization": f"Bearer {self.s.music_api_key}", "x-wait-for-model": "true"}
        try:
            with http_client(self.s.music_timeout_seconds) as client:
                res = client.post(self._url(), json=body, headers=headers)
        except httpx.HTTPError as e:
            raise network_error(e, "music")
        if res.status_code == 404:
            raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, "model not found",
                                message=f"The configured music model ({self.model}) isn't available on the provider. An administrator can change MUSIC_MODEL.")
        raise_for_provider_status(res, "music")
        ctype = res.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in AUDIO_EXT or not res.content:
            raise ProviderError(ErrorCode.GENERATION_FAILED, f"unexpected content-type {ctype!r}", transient=True,
                                message="The music provider didn't return audio. Please try again.")
        ext, mime = AUDIO_EXT[ctype]
        from .. import media
        data, ext, mime, seconds, notes = media.fit_audio_duration(res.content, ext, float(duration))      # MusicGen only approximates the length: make it exact
        return ProviderResult(file=(data, ext, mime), duration_seconds=seconds or float(duration), title=None,
                              meta={"model": self.model, "requested_duration": duration, "bytes": len(data), "notes": notes})


def build_music_provider() -> MusicProvider:
    return HuggingFaceMusicProvider()
