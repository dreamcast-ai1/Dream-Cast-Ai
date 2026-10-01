"""Image generation providers. ImageProvider is the interface the app knows; FalImageProvider is the one vendor adapter
(fal.ai queue API, default model FLUX schnell). To add another vendor, subclass ImageProvider and register it in registry.py."""
from abc import abstractmethod

from ..config import Settings, get_settings
from ..generators import ASPECT_RATIOS
from .base import ErrorCode, GenerationRequest, Provider, ProviderCapability, ProviderError, ProviderResult
from .fal import FalQueueProvider

# fal's named image sizes for our three aspect ratios (sizes are chosen by the provider; we never stretch an image)
IMAGE_SIZES = {"16:9": "landscape_16_9", "9:16": "portrait_16_9", "1:1": "square_hd"}


class ImageProvider(Provider):
    capability = ProviderCapability.IMAGE
    generators = frozenset({"image"})
    label = "Image"
    poll_seconds = None
    not_configured_message = "Image generation is currently unavailable because the image provider has not been configured."

    @abstractmethod
    def supported_aspect_ratios(self) -> list[str]: ...

    def adapt_options(self, generator: str, options: dict) -> list[str]:
        ratio = options.get("aspect_ratio")
        if ratio and ratio not in self.supported_aspect_ratios():
            options["aspect_ratio"] = "1:1"
            return [f"The image provider doesn't support {ratio}. Using 1:1."]
        return []


class FalImageProvider(FalQueueProvider, ImageProvider):
    what = "image"

    def __init__(self, settings: Settings | None = None):
        FalQueueProvider.__init__(self)
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self.s.image_provider}-image"

    @property
    def api_key(self) -> str:
        return self.s.image_provider_api_key or self.s.video_provider_api_key      # same fal.ai account unless a separate key is set

    base_url = property(lambda self: self.s.image_provider_base_url)
    timeout = property(lambda self: 120.0)
    max_download_bytes = property(lambda self: self.s.image_max_download_mb * 1024 * 1024)
    poll_seconds = property(lambda self: self.s.image_poll_seconds)

    def validate_config(self) -> list[str]:
        problems = []
        if self.s.image_provider != "fal":
            problems.append("IMAGE_PROVIDER must be: fal")
        if not self.api_key:
            problems.append("IMAGE_PROVIDER_API_KEY (or VIDEO_PROVIDER_API_KEY) is not set")
        if not self.s.image_provider_model:
            problems.append("IMAGE_PROVIDER_MODEL is not set")
        return problems

    def is_configured(self) -> bool:
        return not self.validate_config()

    def supported_aspect_ratios(self) -> list[str]:
        return list(ASPECT_RATIOS)

    def info(self) -> dict:
        return {"model": self.s.image_provider_model, "aspect_ratios": self.supported_aspect_ratios(), "key_configured": bool(self.api_key)}

    def build_request(self, request: GenerationRequest) -> tuple[str, dict]:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        ratio = request.options.get("aspect_ratio") or "1:1"
        if ratio not in IMAGE_SIZES:
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"aspect {ratio} unsupported",
                                message=f"The image provider supports these formats: {', '.join(IMAGE_SIZES)}.")
        return self.s.image_provider_model, {"prompt": request.refined_prompt.strip()[:2000], "image_size": IMAGE_SIZES[ratio], "num_images": 1}

    def output_url(self, result: dict) -> str | None:
        img = (result.get("images") or [None])[0] or result.get("image") or {}
        return img.get("url") if isinstance(img, dict) else None

    def download_result(self, external_id: str, request: GenerationRequest) -> ProviderResult:
        result = super().download_result(external_id, request)
        result.meta = {**(result.meta or {}), "model": self.s.image_provider_model}       # recorded with the asset
        return result


def build_image_provider() -> ImageProvider:
    return FalImageProvider()
