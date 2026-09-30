"""Text generation (story / script / lyrics) through the same LLM configuration used for prompt refinement."""
from ..config import get_settings
from ..textparse import clean_generated, parse_title
from .base import ErrorCode, GenerationRequest, ProviderCapability, ProviderError, ProviderResult, SyncProvider
from .text import PromptRefinementProvider
from .text_formats import BUILDERS


class TextGenerationProvider(SyncProvider):
    capability = ProviderCapability.TEXT
    generators = frozenset(BUILDERS)
    label = "Text generation"
    not_configured_message = "Text generation provider is not configured."

    def __init__(self):
        super().__init__()
        self._llm = PromptRefinementProvider()

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self._llm.name}-text"

    def is_configured(self) -> bool:
        return self._llm.is_configured()

    def validate_config(self) -> list[str]:
        return self._llm.validate_config()

    def info(self) -> dict:
        return {"model": self._llm.model, "languages": ["English", "Hindi", "Telugu"]}

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        s = get_settings()
        job = BUILDERS[request.generator_type](request.refined_prompt, request.options, request.context, s.llm_max_output_tokens)
        try:
            raw = self._llm.complete(job.system, job.user, max_tokens=job.max_tokens, temperature=0.8,
                                     timeout=s.llm_generation_timeout_seconds)
        except ProviderError as e:
            label = request.generator_type
            msgs = {ErrorCode.PROVIDER_UNAVAILABLE: f"{label.capitalize()} generation failed because the text provider is unavailable.",
                    ErrorCode.RATE_LIMITED: f"{label.capitalize()} generation failed because the text provider is rate-limiting requests. Try again shortly.",
                    ErrorCode.AUTHENTICATION_ERROR: "The text provider rejected its API key. An administrator needs to check LLM_API_KEY."}
            if e.code in msgs:
                raise ProviderError(e.code, e.detail, e.transient, msgs[e.code])
            raise
        text = clean_generated(raw)
        if len(text) < 20:
            raise ProviderError(ErrorCode.GENERATION_FAILED, "text provider returned almost nothing", transient=True,
                                message="The text provider returned an empty result. Please try again.")
        return ProviderResult(text=text, title=parse_title(text), language=job.language,
                              meta={"model": self._llm.model, "language": job.language, "target_words": job.words})
