"""Video generation providers. VideoProvider is the interface the rest of the app knows; FalVideoProvider is the one vendor
adapter (fal.ai queue API). To add another vendor, subclass VideoProvider and register it in registry.py."""
import math
from abc import abstractmethod

from ..config import Settings, get_settings
from .base import ErrorCode, GenerationRequest, ProviderCapability, ProviderError
from .fal import FalQueueProvider, data_uri, read_reference
from ..generators import ASPECT_RATIOS
from .base import Provider

DURATIONS = (5, 10, 15)
IMAGE_METHOD = "Image to Video"


def _ratio(label: str) -> float:
    w, h = label.split(":")
    return int(w) / int(h)


def closest_aspect(requested: str, supported: list[str]) -> str:
    """Nearest supported aspect ratio (by log distance), so nothing is stretched."""
    return min(supported, key=lambda s: abs(math.log(_ratio(s) / _ratio(requested))))


class VideoProvider(Provider):
    """Interface: validate_config / generate / get_status / download_result / cancel (inherited from Provider) plus capabilities."""
    capability = ProviderCapability.VIDEO
    generators = frozenset({"video"})
    label = "Video"
    poll_seconds = None
    not_configured_message = "Video generation is currently unavailable because the video provider has not been configured."

    @abstractmethod
    def supported_durations(self) -> list[int]: ...

    @abstractmethod
    def supported_aspect_ratios(self) -> list[str]: ...

    @property
    @abstractmethod
    def supports_image_to_video(self) -> bool: ...

    supports_reference_images = False     # multi-image references; single source image for image-to-video is separate

    def adapt_options(self, generator: str, options: dict) -> list[str]:
        notes, allowed = [], self.supported_durations()
        d = options.get("duration_seconds")
        if d is not None and d not in allowed:
            options["duration_seconds"] = max(allowed)
            notes.append(f"The video provider supports clips up to {max(allowed)} seconds. Duration adjusted to {max(allowed)} seconds.")
        ratio, ratios = options.get("aspect_ratio"), self.supported_aspect_ratios()
        if ratio and ratio not in ratios:
            mapped = closest_aspect(ratio, ratios)
            options["aspect_ratio"] = mapped
            notes.append(f"The video provider doesn't support {ratio}. Using the closest supported format, {mapped} (nothing is stretched).")
        return notes

    def validate_options(self, generator: str, options: dict, text: str = "", refs: list[dict] | None = None) -> list[str]:
        notes = []
        if options.get("method") == IMAGE_METHOD:
            if not self.supports_image_to_video:
                raise ProviderError(ErrorCode.INVALID_REQUEST, "no i2v model", message="This provider does not support image-to-video.")
            if not any((r.get("mime_type") or "").startswith("image/") for r in (refs or [])):
                raise ProviderError(ErrorCode.INVALID_REQUEST, "no source image", message="Choose or upload an image for image-to-video.")
        elif refs:
            notes.append("This provider can't take reference images for text-to-video, so references are described in the prompt instead.")
        d = options.get("duration_seconds")
        if d is not None and d not in self.supported_durations():
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"duration {d} unsupported",
                                message=f"The configured video provider supports clips of {', '.join(map(str, self.supported_durations()))} seconds "
                                        f"(maximum {max(self.supported_durations())}).")
        ratio = options.get("aspect_ratio")
        if ratio and ratio not in self.supported_aspect_ratios():
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"aspect {ratio} unsupported",
                                message=f"The configured video provider supports these formats: {', '.join(self.supported_aspect_ratios())}.")
        return notes


class FalVideoProvider(FalQueueProvider, VideoProvider):
    """fal.ai hosted video models. Defaults target Kling v3 Standard (text-to-video and image-to-video, durations 3-15 s; the app uses 10).
    Payload field names (prompt, duration, aspect_ratio, generate_audio, start_image_url) follow the Kling v3 schema as published by fal.ai; older Kling
    models name the source image `image_url` and have no generate_audio (handled below). Other models may need small changes in
    build_request."""

    def __init__(self, settings: Settings | None = None):
        FalQueueProvider.__init__(self)
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self.s.video_provider}-video"

    @property
    def api_key(self) -> str:
        return self.s.video_provider_api_key

    @property
    def base_url(self) -> str:
        return self.s.video_provider_base_url

    @property
    def timeout(self) -> float:
        return 120.0

    @property
    def max_download_bytes(self) -> int:
        return self.s.video_max_download_mb * 1024 * 1024

    @property
    def poll_seconds(self) -> float:  # type: ignore[override]
        return self.s.video_poll_seconds

    @property
    def model(self) -> str:
        return self.s.video_provider_model

    @property
    def supports_image_to_video(self) -> bool:
        return bool(self.s.video_provider_i2v_model)

    def validate_config(self) -> list[str]:
        problems = []
        if self.s.video_provider != "fal":
            problems.append("VIDEO_PROVIDER must be: fal")
        if not self.s.video_provider_api_key:
            problems.append("VIDEO_PROVIDER_API_KEY is not set")
        if not self.model:
            problems.append("VIDEO_PROVIDER_MODEL is not set")
        return problems

    def is_configured(self) -> bool:
        return not self.validate_config()

    def supported_durations(self) -> list[int]:
        return [d for d in DURATIONS if d <= self.s.video_max_seconds] or [DURATIONS[0]]

    def supported_aspect_ratios(self) -> list[str]:
        return [a for a in (x.strip() for x in self.s.video_aspect_ratios.split(",")) if a in ASPECT_RATIOS] or ["16:9"]

    def info(self) -> dict:
        return {"model": self.model, "i2v_model": self.s.video_provider_i2v_model or None, "durations": self.supported_durations(),
                "aspect_ratios": self.supported_aspect_ratios(), "image_to_video": self.supports_image_to_video,
                "reference_images": False, "credential": "VIDEO_PROVIDER_API_KEY", "key_configured": bool(self.s.video_provider_api_key)}

    def build_request(self, request: GenerationRequest) -> tuple[str, dict]:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        o = request.options
        self.validate_options("video", o, refs=[{"mime_type": r.get("mime_type", "")} for r in request.reference_assets])
        payload = {"prompt": request.refined_prompt.strip()[:2400], "duration": str(int(o.get("duration_seconds") or 10))}
        if o.get("method") == IMAGE_METHOD:
            src = next((r for r in request.reference_assets if (r.get("mime_type") or "").startswith("image/")), None)
            if not src:
                raise ProviderError(ErrorCode.INVALID_REQUEST, "no source image", message="Choose or upload an image for image-to-video.")
            model = self.s.video_provider_i2v_model
            payload["start_image_url" if "/v3/" in model else "image_url"] = data_uri(read_reference(src), src["mime_type"])
            if "/v3/" in model:
                payload["generate_audio"] = self.s.video_generate_audio
            return model, payload
        payload["aspect_ratio"] = o.get("aspect_ratio") or "16:9"
        if "/v3/" in self.model:
            payload["generate_audio"] = self.s.video_generate_audio
        return self.model, payload

    def download_result(self, external_id: str, request: GenerationRequest):
        result = super().download_result(external_id, request)
        used = self.s.video_provider_i2v_model if request.options.get("method") == IMAGE_METHOD else self.model
        result.meta = {**(result.meta or {}), "model": used}                    # recorded with the asset
        return result

    def output_url(self, result: dict) -> str | None:
        v = result.get("video") or (result.get("videos") or [None])[0] or {}
        return v.get("url") if isinstance(v, dict) else None


def build_video_provider() -> VideoProvider:
    return FalVideoProvider()
