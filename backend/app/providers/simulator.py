"""Development simulator. NOT a real generator (avatars only until that provider exists): it exercises the job pipeline (stages, retry, failure,
cancellation, notifications, result storage) and its output is always labelled as simulated.
Enabled only with ENABLE_DEV_SIMULATOR=true. Prompt markers control behaviour for testing:
  [simulate:fail]      permanent failure          [simulate:transient]  fails once, succeeds on the retry
  [simulate:invalid]   INVALID_REQUEST            [simulate:slow]       takes ~30 s (to test cancellation)
  [simulate:nocancel]  provider cannot cancel     [simulate:noquota]    provider quota error
"""
import threading
import time
import uuid

from ..config import get_settings
from .base import (ErrorCode, GenerationRequest, Provider, ProviderCapability, ProviderError, ProviderJobStatus,
                   ProviderResult)


class DevSimulatorProvider(Provider):
    name = "dev-simulator"
    capability = ProviderCapability.SIMULATOR
    # Only the generators that have no real provider yet. Every generator that has one never falls back to fake output.
    generators = frozenset({"ai_avatar", "interactive_avatar"})
    supports_cancel = True
    simulated = True

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def is_configured(self) -> bool:
        return True

    def validate_config(self) -> list[str]:
        return []

    def generate(self, request: GenerationRequest) -> str:
        text = request.refined_prompt.lower()
        if "[simulate:invalid]" in text:
            raise ProviderError(ErrorCode.INVALID_REQUEST, "simulated invalid request")
        if "[simulate:noquota]" in text:
            raise ProviderError(ErrorCode.QUOTA_EXCEEDED, "simulated quota")
        if "[simulate:transient]" in text and request.attempt == 1:
            raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, "simulated outage", transient=True)
        step = get_settings().dev_simulator_step_seconds
        total = 30.0 if "[simulate:slow]" in text else step * 2
        ext = uuid.uuid4().hex
        with self._lock:
            self._jobs[ext] = {"start": time.monotonic(), "step": step, "total": total, "cancelled": False,
                               "fail": "[simulate:fail]" in text, "nocancel": "[simulate:nocancel]" in text,
                               "req": request}
        return ext

    def get_status(self, external_id: str) -> ProviderJobStatus:
        with self._lock:
            j = self._jobs.get(external_id)
        if not j:
            return ProviderJobStatus("FAILED", error=ProviderError(ErrorCode.GENERATION_FAILED, "simulator lost job"))
        elapsed = time.monotonic() - j["start"]
        if elapsed < j["total"]:
            return ProviderJobStatus("PROCESSING", stage="GENERATING" if elapsed < j["total"] / 2 else "PROCESSING")
        if j["fail"]:
            return ProviderJobStatus("FAILED", error=ProviderError(ErrorCode.GENERATION_FAILED, "simulated failure"))
        req: GenerationRequest = j["req"]
        note = ("[SIMULATED OUTPUT] No real content was generated. This entry only demonstrates the job pipeline.\n\n"
                f"Generator: {req.generator_type}\nPrompt: {req.refined_prompt}")
        return ProviderJobStatus("COMPLETED", result=ProviderResult(text=note, simulated=True, meta={"simulated": True}))

    def cancel(self, external_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(external_id)
            if not j or j["nocancel"]:
                return False
            j["cancelled"] = True
            return True
