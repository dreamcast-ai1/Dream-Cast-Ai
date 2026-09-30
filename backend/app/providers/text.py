"""Text/LLM provider used for prompt refinement. One class covers every OpenAI-compatible chat API."""
import logging
from abc import abstractmethod

import httpx

from ..config import Settings, get_settings
from .base import ErrorCode, Provider, ProviderCapability, ProviderError

log = logging.getLogger("dreamcast.llm")

# provider -> (base_url, default_model, needs_key). Model names change over time; override with LLM_MODEL.
PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant", True),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash", True),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.1-8b-instruct:free", True),
    "ollama": ("http://localhost:11434/v1", "llama3.2", False),
    "custom": ("", "", True),
}


class TextProvider(Provider):
    capability = ProviderCapability.TEXT

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 400, temperature: float = 0.4, timeout: float | None = None) -> str: ...

    # Text providers are used synchronously for refinement, not as background generators (yet).
    def generate(self, request):  # pragma: no cover - not used until story/script/lyrics providers
        raise ProviderError(ErrorCode.INVALID_REQUEST, "Text provider is not a generation provider")

    def get_status(self, external_id):  # pragma: no cover
        raise NotImplementedError

    def cancel(self, external_id):  # pragma: no cover
        return False


def http_client(timeout: float) -> httpx.Client:
    """Factory kept separate so tests can inject a mock transport."""
    return httpx.Client(timeout=timeout)   # (music/voice use providers/http_util.http_client)


class PromptRefinementProvider(TextProvider):
    """OpenAI-compatible chat-completions client. The API key is read from server settings only."""
    label = "Prompt refinement"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.s.llm_provider

    @property
    def base_url(self) -> str:
        return (self.s.llm_base_url or PRESETS.get(self.s.llm_provider, PRESETS["custom"])[0]).rstrip("/")

    @property
    def model(self) -> str:
        return self.s.llm_model or PRESETS.get(self.s.llm_provider, PRESETS["custom"])[1]

    def needs_key(self) -> bool:
        return PRESETS.get(self.s.llm_provider, PRESETS["custom"])[2]

    def validate_config(self) -> list[str]:
        problems = []
        if self.s.llm_provider not in PRESETS:
            problems.append(f"LLM_PROVIDER must be one of: {', '.join(PRESETS)}")
        if self.needs_key() and not self.s.llm_api_key:
            problems.append("LLM_API_KEY is not set")
        if not self.base_url:
            problems.append("LLM_BASE_URL is not set")
        if not self.model:
            problems.append("LLM_MODEL is not set")
        return problems

    def is_configured(self) -> bool:
        return not self.validate_config()

    def complete(self, system: str, user: str, max_tokens: int = 400, temperature: float = 0.4, timeout: float | None = None) -> str:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()))
        headers = {"Content-Type": "application/json"}
        if self.s.llm_api_key:
            headers["Authorization"] = f"Bearer {self.s.llm_api_key}"
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            with http_client(timeout or self.s.llm_timeout_seconds) as client:
                res = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"timeout: {e}", transient=True)
        except httpx.HTTPError as e:
            raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"network error: {type(e).__name__}", transient=True)
        if res.status_code in (401, 403):
            raise ProviderError(ErrorCode.AUTHENTICATION_ERROR, f"HTTP {res.status_code}")
        if res.status_code == 429:
            raise ProviderError(ErrorCode.RATE_LIMITED, "HTTP 429", transient=True)
        if res.status_code >= 500:
            raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"HTTP {res.status_code}", transient=True)
        if res.status_code >= 400:
            log.warning("LLM rejected request: HTTP %s %s", res.status_code, res.text[:300])
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"HTTP {res.status_code}")
        try:
            text = res.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProviderError(ErrorCode.UNKNOWN_ERROR, "unexpected response shape")
        text = (text or "").strip()
        if not text:
            raise ProviderError(ErrorCode.UNKNOWN_ERROR, "empty completion")
        return text
