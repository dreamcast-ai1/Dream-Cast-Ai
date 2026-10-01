"""Provider abstraction. Every external AI service is wrapped in a Provider subclass so it can be swapped
without touching routers, the job runner or the UI.

  Provider (= BaseGenerationProvider)   generate / get_status / cancel / validate_config
    ├─ TextProvider                     adds complete(); used by prompt refinement (text.py)
    └─ concrete generators (video/music/voice/...) arrive in later phases; they only implement this interface.
"""
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ProviderCapability(str, Enum):
    VIDEO = "video"
    MUSIC = "music"
    VOICE = "voice"
    TEXT = "text"      # LLM text (prompt refinement, later story/script/lyrics)
    FACE = "face"
    IMAGE = "image"
    AVATAR = "avatar"
    SIMULATOR = "simulator"
    # (music and voice providers use MUSIC / VOICE; text generation uses TEXT)


class ErrorCode(str, Enum):
    API_NOT_CONFIGURED = "API_NOT_CONFIGURED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    GENERATION_FAILED = "GENERATION_FAILED"
    STORAGE_ERROR = "STORAGE_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


# Human-readable text shown to users. Technical details go to the server log only.
USER_MESSAGES = {
    ErrorCode.API_NOT_CONFIGURED: "This generator isn't set up yet. An administrator needs to configure a provider.",
    ErrorCode.PROVIDER_UNAVAILABLE: "The AI provider is temporarily unavailable. Please try again in a few minutes.",
    ErrorCode.INVALID_REQUEST: "The provider couldn't accept this request. Try editing the prompt or options.",
    ErrorCode.QUOTA_EXCEEDED: "The provider's usage quota has been reached. Please try again later.",
    ErrorCode.AUTHENTICATION_ERROR: "The provider rejected DreamCast's credentials. An administrator needs to check the API key.",
    ErrorCode.RATE_LIMITED: "The provider is receiving too many requests. Please try again shortly.",
    ErrorCode.GENERATION_FAILED: "The generation didn't complete. You can retry it.",
    ErrorCode.STORAGE_ERROR: "The result was created but couldn't be saved. Please try again.",
    ErrorCode.UNKNOWN_ERROR: "Something went wrong while generating. Please try again.",
}

# Failures where the provider never produced work for the user: the daily allowance is given back.
REFUNDABLE = {ErrorCode.API_NOT_CONFIGURED, ErrorCode.INVALID_REQUEST, ErrorCode.QUOTA_EXCEEDED,
              ErrorCode.AUTHENTICATION_ERROR, ErrorCode.RATE_LIMITED}


class ProviderError(Exception):
    """Raised by providers. `transient` errors are eligible for the single automatic retry."""

    def __init__(self, code: ErrorCode, detail: str = "", transient: bool = False, message: str | None = None):
        super().__init__(detail or code.value)
        self.code, self.detail, self.transient, self.message = code, detail, transient, message

    @property
    def user_message(self) -> str:
        """Specific, user-safe text if the provider supplied one, otherwise the generic text for the error code."""
        return self.message or USER_MESSAGES[self.code]


@dataclass
class GenerationRequest:
    """The standard request every generator receives (mirrors the GenerationJob row)."""
    id: str
    user_id: str
    project_id: str | None
    generator_type: str
    original_prompt: str
    refined_prompt: str
    options: dict
    reference_assets: list[dict]      # [{id, name, type, path, mime_type}]
    context: dict
    provider: str | None
    status: str
    attempt: int
    created_at: datetime
    updated_at: datetime


@dataclass
class ProviderResult:
    text: str | None = None
    file: tuple[bytes, str, str] | None = None   # (data, extension, mime) - small outputs held in memory
    path: tuple[str, str, str] | None = None     # (temp file path, extension, mime) - large outputs, never loaded into memory
    meta: dict = field(default_factory=dict)
    title: str | None = None
    language: str | None = None
    duration_seconds: float | None = None
    simulated: bool = False
    units: float = 0.0
    cost_estimate: float = 0.0


@dataclass
class ProviderJobStatus:
    state: str                       # PROCESSING | COMPLETED | FAILED
    stage: str = "GENERATING"        # GENERATING | PROCESSING (only what the provider really reports)
    progress: int | None = None      # None unless the provider reports real progress
    result: ProviderResult | None = None
    error: ProviderError | None = None


class Provider(ABC):
    name: str = ""
    label: str = ""                  # human role shown in the UI, e.g. "Music"
    resumable: bool = False          # True if a job can resume polling by external id after a worker restart (no resubmission)
    poll_seconds: float | None = None   # provider-specific polling interval; None = the global default
    capability: ProviderCapability
    generators: frozenset[str] = frozenset()   # generator ids this provider can run
    supports_cancel: bool = False
    simulated: bool = False
    not_configured_message: str = "This generator isn't set up yet. An administrator needs to configure a provider."

    def info(self) -> dict:
        """Non-secret facts for the UI/admin (model, supported durations/languages...). Never include keys."""
        return {}

    def adapt_options(self, generator: str, options: dict) -> list[str]:
        """Refine step only: adjust options the provider can't honour exactly (e.g. cap a duration, map an aspect ratio) and
        return user-facing notes. Mutates `options`. Submission never adapts silently: it validates."""
        return []

    def validate_options(self, generator: str, options: dict, text: str = "", refs: list[dict] | None = None) -> list[str]:
        """Check the request against this provider's real capabilities *before* calling it. Raise
        ProviderError(INVALID_REQUEST, message=...) if unsupported; return human-readable notes about fallbacks.
        `refs` describes the selected reference files: [{id, type, mime_type}]."""
        return []

    def download_result(self, external_id: str, request: "GenerationRequest") -> "ProviderResult":
        """Fetch the finished output (streamed to a temporary file) once get_status reports COMPLETED without a result."""
        raise NotImplementedError

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def validate_config(self) -> list[str]:
        """Human-readable configuration problems (empty list = OK). Never include secret values."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> str:
        """Start generation and return the provider's external job id. Must not block for the whole job."""

    @abstractmethod
    def get_status(self, external_id: str) -> ProviderJobStatus: ...

    @abstractmethod
    def cancel(self, external_id: str) -> bool:
        """Ask the provider to stop. Return True only if the provider confirmed cancellation."""


BaseGenerationProvider = Provider


class SyncProvider(Provider):
    """For providers whose API answers in one blocking HTTP call (LLM, TTS, ...). The runner still treats them like any
    other provider: generate() -> external id, get_status() -> COMPLETED. The work happens inside a worker thread,
    never in a web request."""

    def __init__(self):
        self._done: dict[str, ProviderResult] = {}

    @abstractmethod
    def run(self, request: GenerationRequest) -> ProviderResult: ...

    def generate(self, request: GenerationRequest) -> str:
        ext = uuid.uuid4().hex
        self._done[ext] = self.run(request)      # raises ProviderError on failure (handled by the runner)
        return ext

    def get_status(self, external_id: str) -> ProviderJobStatus:
        result = self._done.pop(external_id, None)
        if result is None:
            return ProviderJobStatus("FAILED", error=ProviderError(ErrorCode.GENERATION_FAILED, "result lost (worker restarted)", transient=True))
        return ProviderJobStatus("COMPLETED", result=result)

    def cancel(self, external_id: str) -> bool:
        return False
