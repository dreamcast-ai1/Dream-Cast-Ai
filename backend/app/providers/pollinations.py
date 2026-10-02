"""Pollinations (https://gen.pollinations.ai) image and video providers.

Both are plain authenticated GET requests that answer with the finished file in ONE response (`GET /image/{prompt}`, `GET /video/{prompt}`),
so they are SyncProviders: the work happens inside a background worker thread, never in a web request. Image and video use separate
secret keys (POLLINATIONS_IMAGE_API_KEY / POLLINATIONS_VIDEO_API_KEY), sent only in the Authorization header. Nothing here reaches the browser."""
import logging
import os
import tempfile
from urllib.parse import quote

import httpx

from ..config import Settings, get_settings
from .base import ErrorCode, GenerationRequest, ProviderError, ProviderResult, SyncProvider
from .http_util import http_client, network_error, raise_for_provider_status
from .image import ImageProvider
from .video import VideoProvider, closest_aspect

log = logging.getLogger("dreamcast.pollinations")
IMAGE_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024)}
VIDEO_RATIOS = ["16:9", "9:16"]
MAX_PROMPT_CHARS = 1500           # the prompt travels in the URL path
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm"}


def _rm(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _chosen(setting: str, fal_has_key: bool) -> bool:
    """Pollinations runs when chosen explicitly, or when the default ('fal') has no key to use while Pollinations does."""
    return setting == "pollinations" or (setting == "fal" and not fal_has_key)


class _PollinationsFetch:
    """The shared GET -> temporary file step with the error mapping every provider needs."""
    what = "image"
    kind = "image/"

    def _fetch(self, url: str, params: dict, key: str, timeout: float, max_bytes: int) -> tuple[str, str, int]:
        tmp = tempfile.NamedTemporaryFile(prefix="dc_pol_", delete=False)
        size, ctype = 0, ""
        try:
            with http_client(timeout) as client, client.stream("GET", url, params=params, headers={"Authorization": f"Bearer {key}"}) as res:
                if res.status_code >= 400:
                    res.read()
                    raise_for_provider_status(res, self.what)
                ctype = res.headers.get("content-type", "").split(";")[0].strip().lower()
                if not ctype.startswith(self.kind):
                    raise ProviderError(ErrorCode.GENERATION_FAILED, f"unexpected content-type {ctype!r}", transient=True,
                                        message=f"The {self.what} provider didn't return {'an image' if self.what == 'image' else 'a video'}. Please try again.")
                for chunk in res.iter_bytes(1024 * 256):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ProviderError(ErrorCode.STORAGE_ERROR, "download too large", message=f"The generated {self.what} is larger than this server allows.")
                    tmp.write(chunk)
            tmp.close()
        except httpx.HTTPError as e:
            tmp.close()
            _rm(tmp.name)
            raise network_error(e, self.what)
        except ProviderError:
            tmp.close()
            _rm(tmp.name)
            raise
        if not size:
            _rm(tmp.name)
            raise ProviderError(ErrorCode.GENERATION_FAILED, "empty download", transient=True, message=f"The {self.what} provider returned an empty file.")
        return tmp.name, ctype, size


class PollinationsImageProvider(_PollinationsFetch, ImageProvider, SyncProvider):
    what, kind = "image", "image/"

    def __init__(self, settings: Settings | None = None):
        SyncProvider.__init__(self)
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return "pollinations-image"

    def validate_config(self) -> list[str]:
        s = self.s
        if not s.pollinations_image_api_key:
            return ["POLLINATIONS_IMAGE_API_KEY is not set"]
        if not _chosen(s.image_provider, bool(s.image_provider_api_key or s.video_provider_api_key)):
            return ["IMAGE_PROVIDER selects another provider"]
        return []

    def is_configured(self) -> bool:
        return not self.validate_config()

    def supported_aspect_ratios(self) -> list[str]:
        return list(IMAGE_SIZES)

    def info(self) -> dict:
        return {"model": self.s.pollinations_image_model or "provider default", "aspect_ratios": self.supported_aspect_ratios(),
                "key_configured": bool(self.s.pollinations_image_api_key)}

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        s = self.s
        ratio = request.options.get("aspect_ratio") or "1:1"
        if ratio not in IMAGE_SIZES:
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"aspect {ratio} unsupported", message=f"The image provider supports these formats: {', '.join(IMAGE_SIZES)}.")
        w, h = IMAGE_SIZES[ratio]
        params = {"width": w, "height": h, **({"model": s.pollinations_image_model} if s.pollinations_image_model else {})}
        prompt = " ".join(request.refined_prompt.split())[:MAX_PROMPT_CHARS]
        url = f"{s.pollinations_base_url.rstrip('/')}/image/{quote(prompt, safe='')}"
        path, ctype, size = self._fetch(url, params, s.pollinations_image_api_key, s.pollinations_image_timeout_seconds, s.image_max_download_mb * 1024 * 1024)
        log.info("pollinations image ok (%s bytes)", size)
        return ProviderResult(path=(path, EXT.get(ctype, ".png"), ctype), meta={"model": s.pollinations_image_model or "default", "provider": "pollinations", "bytes": size})


class PollinationsVideoProvider(_PollinationsFetch, VideoProvider, SyncProvider):
    """Text-to-video. Pollinations makes clips of a model-specific length (a few seconds), so the clip's real duration is read from the file and
    used by the movie assembly; the 10 s the form asks for is a ceiling, not a promise."""
    what, kind = "video", "video/"

    def __init__(self, settings: Settings | None = None):
        SyncProvider.__init__(self)
        self._settings = settings

    @property
    def s(self) -> Settings:
        return self._settings or get_settings()

    @property
    def name(self) -> str:  # type: ignore[override]
        return "pollinations-video"

    def validate_config(self) -> list[str]:
        s = self.s
        if not s.pollinations_video_api_key:
            return ["POLLINATIONS_VIDEO_API_KEY is not set"]
        if not _chosen(s.video_provider, bool(s.video_provider_api_key)):
            return ["VIDEO_PROVIDER selects another provider"]
        return []

    def is_configured(self) -> bool:
        return not self.validate_config()

    def supported_durations(self) -> list[int]:
        return [10]          # the form's lowest step; the provider decides the real (shorter) length

    def supported_aspect_ratios(self) -> list[str]:
        return list(VIDEO_RATIOS)

    @property
    def supports_image_to_video(self) -> bool:      # type: ignore[override]
        return False         # Pollinations needs a public image URL; our uploads are private, so this is never faked

    def info(self) -> dict:
        return {"model": self.s.pollinations_video_model or "provider default", "durations": self.supported_durations(), "aspect_ratios": self.supported_aspect_ratios(),
                "image_to_video": False, "reference_images": False, "key_configured": bool(self.s.pollinations_video_api_key)}

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        s, o = self.s, request.options
        self.validate_options("video", o, refs=[{"mime_type": r.get("mime_type", "")} for r in request.reference_assets])
        ratio = o.get("aspect_ratio") or "16:9"
        params = {"aspectRatio": ratio if ratio in VIDEO_RATIOS else closest_aspect(ratio, VIDEO_RATIOS),
                  **({"model": s.pollinations_video_model} if s.pollinations_video_model else {}),
                  **({"duration": s.pollinations_video_seconds} if s.pollinations_video_seconds > 0 else {})}
        prompt = " ".join(request.refined_prompt.split())[:MAX_PROMPT_CHARS]
        url = f"{s.pollinations_base_url.rstrip('/')}/video/{quote(prompt, safe='')}"
        path, ctype, size = self._fetch(url, params, s.pollinations_video_api_key, s.pollinations_video_timeout_seconds, s.video_max_download_mb * 1024 * 1024)
        from .. import media
        info = media.probe(path)
        if info is None or not info.width:
            _rm(path)
            raise ProviderError(ErrorCode.GENERATION_FAILED, "downloaded video is not decodable", transient=True,
                                message="The video provider returned a file that can't be played. Please try again.")
        log.info("pollinations video ok (%s bytes, %.1fs)", size, info.duration or 0)
        return ProviderResult(path=(path, EXT.get(ctype, ".mp4"), ctype), duration_seconds=info.duration,
                              meta={"model": s.pollinations_video_model or "default", "provider": "pollinations", "bytes": size, "clip_seconds": info.duration})
