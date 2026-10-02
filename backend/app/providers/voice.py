"""Voice / text-to-speech providers. The app asks for gender, accent, emotion and language; each provider maps them
to what it truly supports and reports every approximation as a note, never silently."""
import base64
from abc import abstractmethod

import httpx

from ..config import Settings, get_settings
from .base import ErrorCode, GenerationRequest, ProviderCapability, ProviderError, ProviderResult, SyncProvider
from .http_util import http_client, network_error, raise_for_provider_status

ACCENT_LOCALE = {"Indian English": "en-IN", "American": "en-US", "British": "en-GB", "Hindi": "hi-IN", "Telugu": "te-IN", "Other": "en-US"}
LANGUAGE_LOCALE = {"English": None, "Hindi": "hi-IN", "Telugu": "te-IN"}
LOCALE_LANGUAGE = {"en-IN": "English", "en-US": "English", "en-GB": "English", "hi-IN": "Hindi", "te-IN": "Telugu"}


class VoiceProvider(SyncProvider):
    capability = ProviderCapability.VOICE
    generators = frozenset({"voice"})
    label = "Voice"
    not_configured_message = "Voice provider is not configured. An administrator needs to set VOICE_API_KEY."

    @abstractmethod
    def supported_locales(self) -> list[str]: ...

    def supported_languages(self) -> list[str]:
        return sorted({LOCALE_LANGUAGE[l] for l in self.supported_locales() if l in LOCALE_LANGUAGE}, key=["English", "Hindi", "Telugu"].index)

    def max_text_bytes(self) -> int:
        return 4800

    def resolve_locale(self, options: dict) -> tuple[str, list[str]]:
        """Chooses the voice locale from language + accent. Returns (locale, notes). Raises if the language is unsupported."""
        notes: list[str] = []
        accent, language = options.get("accent"), options.get("language")
        locale = LANGUAGE_LOCALE.get(language) if language else None
        if locale:
            if accent and ACCENT_LOCALE.get(accent, locale) != locale:
                notes.append(f"Accent '{accent}' was ignored because the text is in {language}.")
        elif accent in ("Hindi", "Telugu"):
            locale = ACCENT_LOCALE[accent]
            notes.append(f"Speaking in {accent} because that accent was chosen.")
        else:
            locale = ACCENT_LOCALE.get(accent or "Indian English", "en-US")
            if accent == "Other":
                notes.append("Accent 'Other' isn't a specific voice; the closest supported accent (American English) was used.")
        if locale not in self.supported_locales():
            lang = LOCALE_LANGUAGE.get(locale, "that language")
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"locale {locale} unsupported",
                                message=f"The configured voice provider doesn't support {lang}. Supported languages: "
                                        f"{', '.join(self.supported_languages())}.")
        return locale, notes

    @staticmethod
    def target_seconds(options: dict) -> int | None:
        try:
            v = int(options.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            return None
        return v if v in (5, 10, 15) else None

    def fit_length(self, data: bytes, options: dict, notes: list[str]) -> tuple[bytes, float | None]:
        """Honours the optional spoken length: silence pads short speech, a slight speed-up fits slightly long speech, and speech is never cut off."""
        from .. import media
        target = self.target_seconds(options)
        if not target:
            return data, None
        data, _, _, seconds, extra = media.fit_audio_duration(data, ".mp3", float(target), allow_speedup=True)
        notes.extend(extra)
        return data, seconds

    def validate_options(self, generator: str, options: dict, text: str = "", refs: list[dict] | None = None) -> list[str]:
        _, notes = self.resolve_locale(options)
        if text and len(text.encode("utf-8")) > self.max_text_bytes():
            raise ProviderError(ErrorCode.INVALID_REQUEST, "text too long",
                                message="This text is too long for the voice provider. Choose a scene or a shorter excerpt.")
        return notes


# emotion -> (speaking rate, pitch in semitones). Google has no emotion control, so prosody is the honest approximation.
EMOTION_PROSODY = {"Neutral": (1.0, 0.0), "Happy": (1.06, 2.0), "Sad": (0.88, -3.0), "Angry": (1.10, 1.0), "Excited": (1.16, 3.0),
                   "Fearful": (1.12, 2.5), "Calm": (0.93, -1.0), "Serious": (0.95, -1.5)}
GOOGLE_VOICES = {
    ("en-IN", "Female"): "en-IN-Neural2-A", ("en-IN", "Male"): "en-IN-Neural2-B",
    ("en-US", "Female"): "en-US-Neural2-F", ("en-US", "Male"): "en-US-Neural2-D",
    ("en-GB", "Female"): "en-GB-Neural2-A", ("en-GB", "Male"): "en-GB-Neural2-B",
    ("hi-IN", "Female"): "hi-IN-Neural2-A", ("hi-IN", "Male"): "hi-IN-Neural2-B",
    ("te-IN", "Female"): "te-IN-Standard-A", ("te-IN", "Male"): "te-IN-Standard-B"}


class GoogleTTSProvider(VoiceProvider):
    """Google Cloud Text-to-Speech (REST, API key in a header so it never appears in URLs or logs)."""

    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.s.voice_provider

    def validate_config(self) -> list[str]:
        problems = []
        if self.s.voice_provider != "google":
            problems.append("VOICE_PROVIDER must be: google")
        if not self.s.voice_api_key:
            problems.append("VOICE_API_KEY is not set")
        return problems

    def is_configured(self) -> bool:
        return not self.validate_config()

    def supported_locales(self) -> list[str]:
        return sorted({loc for loc, _ in GOOGLE_VOICES})

    def info(self) -> dict:
        return {"model": "Neural2 / Standard voices", "credential": "VOICE_API_KEY", "languages": self.supported_languages(),
                "voice_controls": {"gender": "supported", "accent": "chosen by locale", "emotion": "approximated (speaking rate and pitch)"}}

    def _url(self) -> str:
        return f"{(self.s.voice_base_url or 'https://texttospeech.googleapis.com').rstrip('/')}/v1/text:synthesize"

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        o, text = request.options, request.refined_prompt.strip()
        notes = self.validate_options("voice", o, text)
        locale, _ = self.resolve_locale(o)
        gender = o.get("gender") or "Female"
        if not o.get("gender"):
            notes.append("No gender chosen; a female voice was used.")
        voice = GOOGLE_VOICES[(locale, gender)]
        emotion = o.get("emotion") or "Neutral"
        rate, pitch = EMOTION_PROSODY[emotion]
        if emotion != "Neutral":
            notes.append(f"Emotion '{emotion}' is approximated with speaking rate and pitch; this provider has no true emotion control.")
        headers = {"X-Goog-Api-Key": self.s.voice_api_key}

        def call(with_pitch: bool) -> httpx.Response:
            audio = {"audioEncoding": "MP3", "speakingRate": rate, **({"pitch": pitch} if with_pitch else {})}
            body = {"input": {"text": text}, "voice": {"languageCode": locale, "name": voice}, "audioConfig": audio}
            with http_client(self.s.voice_timeout_seconds) as client:
                return client.post(self._url(), json=body, headers=headers)

        try:
            res = call(True)
            if res.status_code == 400 and "pitch" in res.text.lower():   # some voices reject pitch: retry once without it
                notes.append("This voice doesn't support pitch changes, so only speaking rate was adjusted.")
                res = call(False)
        except httpx.HTTPError as e:
            raise network_error(e, "voice")
        raise_for_provider_status(res, "voice", no_quota_statuses=())
        try:
            data = base64.b64decode(res.json()["audioContent"])
        except (KeyError, ValueError, TypeError):
            data = b""
        if not data:
            raise ProviderError(ErrorCode.GENERATION_FAILED, "no audioContent", transient=True, message="The voice provider didn't return audio. Please try again.")
        data, seconds = self.fit_length(data, o, notes)
        if o.get("voice"):
            notes.append("Named voices aren't used by this provider; the gender/accent voice was used.")
        return ProviderResult(file=(data, ".mp3", "audio/mpeg"), language=LOCALE_LANGUAGE[locale], duration_seconds=seconds,
                              meta={"voice": voice, "locale": locale, "gender": gender, "accent": o.get("accent"), "emotion": emotion,
                                    "notes": notes, "characters": len(text)})


def build_voice_provider() -> VoiceProvider:
    return GoogleTTSProvider()
