"""Text/LLM provider used for prompt refinement. One class covers every OpenAI-compatible chat API."""
import logging
import random
import time
from abc import abstractmethod

import httpx

from ..config import Settings, get_settings
from .base import ErrorCode, Provider, ProviderCapability, ProviderError

log = logging.getLogger("dreamcast.llm")

# provider -> (base_url, default_model, needs_key). Model names change over time; override with LLM_MODEL.
PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant", True),
    # gemini-2.0-flash was shut down by Google on 2026-06-01; this is the current cheap Flash-Lite model (override with LLM_MODEL).
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.5-flash-lite", True),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.1-8b-instruct:free", True),
    "ollama": ("http://localhost:11434/v1", "llama3.2", False),
    "custom": ("", "", True),
}


class TextProvider(Provider):
    capability = ProviderCapability.TEXT

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 400, temperature: float = 0.4, timeout: float | None = None, json_mode: bool = False) -> str: ...

    def generate_text(self, system: str, user: str, *, max_tokens: int = 1500, temperature: float = 0.8, timeout: float | None = None, json_mode: bool = False) -> str:
        """Long-form generation (story, script). Same call as complete() with writing-friendly defaults; complete() stays for prompt refinement.
        json_mode asks the provider for a single JSON object (OpenAI-style response_format); providers that don't support it are retried without it."""
        return self.complete(system, user, max_tokens=max_tokens, temperature=temperature, timeout=timeout, json_mode=json_mode)

    # Text providers are used synchronously for refinement, not as background generators (yet).
    def generate(self, request):  # pragma: no cover - not used until story/script/lyrics providers
        raise ProviderError(ErrorCode.INVALID_REQUEST, "Text provider is not a generation provider")

    def get_status(self, external_id):  # pragma: no cover
        raise NotImplementedError

    def cancel(self, external_id):  # pragma: no cover
        return False


RETRYABLE_STATUS = (429, 500, 502, 503, 504)       # Google: retry these with exponential backoff; never retry 400/401/403/404
_sleep = time.sleep                                  # separate name so tests can replace it


def unavailable_reason(detail: str) -> str:
    """Plain words for why the provider could not be used, from the ProviderError detail (so an overload is not confused with a timeout or a bad key)."""
    d = (detail or "").lower()
    if d.startswith("timeout"):
        return "took too long to answer"
    if d.startswith("network"):
        return "could not be reached"
    return "is temporarily overloaded or unavailable"


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

    def info(self) -> dict:
        return {"model": self.model, "credential": "LLM_API_KEY", "key_configured": bool(self.s.llm_api_key)}

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

    @property
    def reasoning_effort(self) -> str:
        """Gemini models think by default and thinking tokens share the max_tokens budget, so long output could be cut short. Ask for little thinking."""
        return self.s.llm_reasoning_effort or ("low" if self.s.llm_provider == "gemini" else "")

    def _post_with_retries(self, body: dict, headers: dict, timeout: float, max_tokens: int, has_extras: bool) -> httpx.Response:
        """One logical request = up to llm_max_attempts HTTP calls. Only transient failures are retried; the final transient failure is raised with a precise
        detail ("HTTP 503 (after 3 attempts)", "timeout ...", "network error: ...") so callers can word it correctly. A 400 caused by an optional setting
        (reasoning_effort / response_format) is handled inside the attempt by resending once without them; that is not counted as a retry."""
        s = self.s
        attempts, base = max(1, s.llm_max_attempts), max(0.0, s.llm_retry_base_seconds)
        started, budget = time.monotonic(), timeout * 1.5          # overall cap: retries never turn into a hang
        failure: ProviderError | None = None
        res: httpx.Response | None = None
        made = 0                                                   # HTTP attempts actually made
        for attempt in range(1, attempts + 1):
            remaining = budget - (time.monotonic() - started)
            if attempt > 1 and remaining < 3:
                break
            retry_after = 0.0
            made += 1
            try:
                with http_client(min(timeout, max(remaining, 3.0))) as client:
                    res = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                    if res.status_code == 400 and has_extras:
                        for extra in ("reasoning_effort", "response_format"):
                            body.pop(extra, None)
                        body["max_tokens"] = max_tokens
                        has_extras = False
                        res = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            except httpx.TimeoutException as e:
                failure, res = ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"timeout: {type(e).__name__}", transient=True), None
            except httpx.HTTPError as e:
                failure, res = ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"network error: {type(e).__name__}", transient=True), None
            else:
                if res.status_code not in RETRYABLE_STATUS:
                    return res                                     # success, or a non-transient answer the caller handles
                code = ErrorCode.RATE_LIMITED if res.status_code == 429 else ErrorCode.PROVIDER_UNAVAILABLE
                failure = ProviderError(code, f"HTTP {res.status_code}", transient=True)
                try:
                    retry_after = float(res.headers.get("retry-after", 0))
                except ValueError:
                    retry_after = 0.0
            if attempt < attempts and base > 0:
                delay = max(base * 2 ** (attempt - 1) * random.uniform(0.8, 1.2), min(retry_after, 8.0))       # ~1 s, ~2 s ...
                log.warning("LLM transient failure (%s), attempt %s of %s; retrying in %.1fs", failure.detail, attempt, attempts, delay)
                _sleep(delay)
            elif attempt < attempts:
                log.warning("LLM transient failure (%s), attempt %s of %s; retrying", failure.detail, attempt, attempts)
        assert failure is not None
        if made > 1:
            failure.detail = f"{failure.detail} (after {made} attempts)"
        raise failure

    def complete(self, system: str, user: str, max_tokens: int = 400, temperature: float = 0.4, timeout: float | None = None, json_mode: bool = False) -> str:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()))
        headers = {"Content-Type": "application/json"}
        if self.s.llm_api_key:
            headers["Authorization"] = f"Bearer {self.s.llm_api_key}"
        effort = self.reasoning_effort
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens + (1024 if effort else 0),     # headroom for the hidden reasoning tokens
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if effort:
            body["reasoning_effort"] = effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        res = self._post_with_retries(body, headers, timeout or self.s.llm_timeout_seconds, max_tokens, bool(effort or json_mode))
        if res.status_code in (401, 403):
            raise ProviderError(ErrorCode.AUTHENTICATION_ERROR, f"HTTP {res.status_code}")
        if res.status_code == 404:                       # Google answers 404 for a model name it doesn't know (or has retired)
            raise ProviderError(ErrorCode.INVALID_REQUEST, "HTTP 404 (model not found)",
                                message="The text model isn't available. An administrator needs to check LLM_MODEL.")
        if res.status_code >= 400:
            log.warning("LLM rejected request: HTTP %s %s", res.status_code, res.text[:300])
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"HTTP {res.status_code}")
        try:
            choice = res.json()["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProviderError(ErrorCode.UNKNOWN_ERROR, "unexpected response shape")
        if choice.get("finish_reason") == "length":
            log.warning("LLM output was cut off by the token limit (%s tokens requested)", max_tokens)
        text = (text or "").strip()
        if not text:
            raise ProviderError(ErrorCode.UNKNOWN_ERROR, "empty completion")
        return text
