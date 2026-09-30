"""Face replacement providers (creative editing of media the user supplies, with a permission confirmation).
Base interface + one fal.ai adapter (image face swap). No recognition, matching or storage of faces beyond the user's own uploads."""
from abc import abstractmethod

from ..config import Settings, get_settings
from .base import ErrorCode, GenerationRequest, Provider, ProviderCapability, ProviderError
from .fal import FalQueueProvider, data_uri, read_reference


class FaceProvider(Provider):
    capability = ProviderCapability.FACE
    generators = frozenset({"face_replacement"})
    label = "Face replacement"
    not_configured_message = "Face replacement provider is not configured."

    @property
    @abstractmethod
    def supports_video_source(self) -> bool: ...

    def validate_options(self, generator: str, options: dict, text: str = "", refs: list[dict] | None = None) -> list[str]:
        notes = []
        by_id = {r["id"]: r for r in (refs or [])}
        source = by_id.get(options.get("source_asset_id"))
        face = by_id.get(options.get("face_asset_id"))
        if source and (source.get("mime_type") or "").startswith("video/") and not self.supports_video_source:
            raise ProviderError(ErrorCode.INVALID_REQUEST, "video source unsupported",
                                message="This face replacement provider only supports image sources, not video. Choose an image.")
        if face and not (face.get("mime_type") or "").startswith("image/"):
            raise ProviderError(ErrorCode.INVALID_REQUEST, "face must be an image", message="The face must be an image.")
        if text.strip():
            notes.append("This face replacement model doesn't take text instructions; your note is saved with the generation but not sent.")
        return notes


class FalFaceProvider(FalQueueProvider, FaceProvider):
    what = "face replacement"

    def __init__(self, settings: Settings | None = None):
        FalQueueProvider.__init__(self)
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self.s.face_provider}-face"

    api_key = property(lambda self: self.s.face_provider_api_key)
    base_url = property(lambda self: self.s.face_provider_base_url)
    timeout = property(lambda self: 120.0)
    max_download_bytes = property(lambda self: 50 * 1024 * 1024)
    poll_seconds = property(lambda self: self.s.face_poll_seconds)
    supports_video_source = property(lambda self: False)

    def validate_config(self) -> list[str]:
        problems = []
        if self.s.face_provider != "fal":
            problems.append("FACE_PROVIDER must be: fal")
        if not self.s.face_provider_api_key:
            problems.append("FACE_PROVIDER_API_KEY is not set")
        if not self.s.face_provider_model:
            problems.append("FACE_PROVIDER_MODEL is not set")
        return problems

    def is_configured(self) -> bool:
        return not self.validate_config()

    def info(self) -> dict:
        return {"model": self.s.face_provider_model, "sources": ["image"], "key_configured": bool(self.s.face_provider_api_key)}

    def build_request(self, request: GenerationRequest) -> tuple[str, dict]:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        refs = {r["id"]: r for r in request.reference_assets}
        source, face = refs.get(request.options.get("source_asset_id")), refs.get(request.options.get("face_asset_id"))
        if not source or not face:
            raise ProviderError(ErrorCode.INVALID_REQUEST, "missing input", message="Both a source image and a face image are required.")
        self.validate_options("face_replacement", request.options, refs=list(refs.values()))
        return self.s.face_provider_model, {"base_image_url": data_uri(read_reference(source), source["mime_type"]),
                                            "swap_image_url": data_uri(read_reference(face), face["mime_type"])}

    def output_url(self, result: dict) -> str | None:
        img = result.get("image") or (result.get("images") or [None])[0] or {}
        return img.get("url") if isinstance(img, dict) else None


def build_face_provider() -> FaceProvider:
    return FalFaceProvider()
