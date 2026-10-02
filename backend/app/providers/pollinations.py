"""Pollinations (https://gen.pollinations.ai) image and video providers.

Both are plain authenticated GET requests that answer with the finished file in ONE response (`GET /image/{prompt}`, `GET /video/{prompt}`),
so they are SyncProviders: the work happens inside a background worker thread, never in a web request. Image and video use separate
secret keys (POLLINATIONS_IMAGE_API_KEY / POLLINATIONS_VIDEO_API_KEY), sent only in the Authorization header. Nothing here reaches the browser."""
import logging
import os
import re
import tempfile
from urllib.parse import quote

import httpx

from ..config import Settings, get_settings
from .base import ErrorCode, GenerationRequest, ProviderError, ProviderResult, SyncProvider
from .http_util import http_client, network_error, raise_for_provider_status
from .image import ImageProvider
from .video import DURATIONS, VideoProvider, closest_aspect

log = logging.getLogger("dreamcast.pollinations")
IMAGE_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024)}
VIDEO_RATIOS = ["16:9", "9:16"]
MAX_PROMPT_CHARS = 1500           # the prompt travels in the URL path
# What each model family is known to accept. Everything here is OPTIONAL: a parameter that is not listed for the chosen model is simply left out,
# and if Pollinations still answers "does not accept a <name> parameter" the request is retried once more without that parameter.
IMAGE_REJECTS: dict[str, set[str]] = {"tongyi-mai/z-image-turbo": {"resolution", "quality", "duration", "aspectratio"}}
IMAGE_DEFAULT_MODEL = "tongyi-mai/z-image-turbo"            # what Pollinations uses when no model is sent (per its docs)
VIDEO_DURATIONS: dict[str, list[int]] = {"veo": [4, 6, 8]}   # model-name fragment -> seconds the model can make; unknown models get no duration parameter
VIDEO_DEFAULT_MODEL = "google/veo-3.1-fast"
_REJECTED = re.compile(r"does not accept an? [`'\"]?([A-Za-z_]+)[`'\"]? parameter", re.I)
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm"}


def closest(requested: int, options: list[int]) -> int:
    """The supported value nearest the request (ties go to the longer one)."""
    return min(options, key=lambda o: (abs(o - requested), -o))


def video_seconds_for(model: str) -> list[int] | None:
    name = (model or VIDEO_DEFAULT_MODEL).lower()
    return next((v for frag, v in VIDEO_DURATIONS.items() if frag in name), None)


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

    def _fetch_once(self, url: str, params: dict, key: str, timeout: float, max_bytes: int) -> tuple[str, str, int]:
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


    def _fetch(self, url: str, params: dict, key: str, timeout: float, max_bytes: int) -> tuple[str, str, int, list[str]]:
        """Requests the file; if Pollinations rejects an optional parameter by name, drops just that one and asks again (at most twice)."""
        params, dropped = dict(params), []
        for _ in range(3):
            try:
                return (*self._fetch_once(url, params, key, timeout, max_bytes), dropped)
            except ProviderError as e:
                m = _REJECTED.search(e.detail or "") if e.code == ErrorCode.INVALID_REQUEST else None
                name = next((k for k in params if k.lower() == m.group(1).lower()), None) if m else None
                if not name or name == "model":
                    raise
                log.info("pollinations %s: parameter %r not accepted by this model; retrying without it", self.what, name)
                dropped.append(name)
                params.pop(name)
        raise ProviderError(ErrorCode.INVALID_REQUEST, "parameters rejected repeatedly", message=f"The {self.what} provider rejected this request. Try again with a different prompt.")


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
                "credential": "POLLINATIONS_IMAGE_API_KEY", "key_configured": bool(self.s.pollinations_image_api_key)}

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        s = self.s
        ratio = request.options.get("aspect_ratio") or "1:1"
        if ratio not in IMAGE_SIZES:
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"aspect {ratio} unsupported", message=f"The image provider supports these formats: {', '.join(IMAGE_SIZES)}.")
        w, h = IMAGE_SIZES[ratio]
        model = s.pollinations_image_model
        # Image parameters only: width and height. (resolution, duration, aspect ratio, audio... belong to other models and are never sent.)
        params = {"width": w, "height": h, **({"model": model} if model else {})}
        rejected = {k.lower() for k in IMAGE_REJECTS.get(model or IMAGE_DEFAULT_MODEL, set())}
        params = {k: v for k, v in params.items() if k.lower() not in rejected}
        prompt = " ".join(request.refined_prompt.split())[:MAX_PROMPT_CHARS]
        url = f"{s.pollinations_base_url.rstrip('/')}/image/{quote(prompt, safe='')}"
        path, ctype, size, dropped = self._fetch(url, params, s.pollinations_image_api_key, s.pollinations_image_timeout_seconds, s.image_max_download_mb * 1024 * 1024)
        log.info("pollinations image ok (%s bytes)", size)
        return ProviderResult(path=(path, EXT.get(ctype, ".png"), ctype), meta={"model": model or "default", "provider": "pollinations", "bytes": size,
                                                                              **({"dropped_parameters": dropped} if dropped else {})})


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
        return list(DURATIONS)       # 5 / 10 / 15 are always offered; the model's own lengths are mapped to the nearest one (see run)

    def supported_aspect_ratios(self) -> list[str]:
        return list(VIDEO_RATIOS)

    @property
    def supports_image_to_video(self) -> bool:      # type: ignore[override]
        return False         # Pollinations needs a public image URL; our uploads are private, so this is never faked

    def info(self) -> dict:
        return {"model": self.s.pollinations_video_model or "provider default", "durations": self.supported_durations(), "aspect_ratios": self.supported_aspect_ratios(),
                "image_to_video": False, "reference_images": False, "credential": "POLLINATIONS_VIDEO_API_KEY",
                "key_configured": bool(self.s.pollinations_video_api_key)}

    def run(self, request: GenerationRequest) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ErrorCode.API_NOT_CONFIGURED, "; ".join(self.validate_config()), message=self.not_configured_message)
        s, o = self.s, request.options
        self.validate_options("video", o, refs=[{"mime_type": r.get("mime_type", "")} for r in request.reference_assets])
        ratio = o.get("aspect_ratio") or "16:9"
        model = s.pollinations_video_model
        requested = int(o.get("duration_seconds") or 10)
        # Video parameters only: aspectRatio and (when the model's capabilities are known) a duration it supports. Never resolution or image sizes.
        supported = [s.pollinations_video_seconds] if s.pollinations_video_seconds > 0 else video_seconds_for(model)
        provider_seconds = closest(requested, supported) if supported else None
        params = {"aspectRatio": ratio if ratio in VIDEO_RATIOS else closest_aspect(ratio, VIDEO_RATIOS),
                  **({"model": model} if model else {}),
                  **({"duration": provider_seconds} if provider_seconds else {})}
        prompt = " ".join(request.refined_prompt.split())[:MAX_PROMPT_CHARS]
        url = f"{s.pollinations_base_url.rstrip('/')}/video/{quote(prompt, safe='')}"
        path, ctype, size, dropped = self._fetch(url, params, s.pollinations_video_api_key, s.pollinations_video_timeout_seconds, s.video_max_download_mb * 1024 * 1024)
        from .. import media
        info = media.probe(path)
        if info is None or not info.width:
            _rm(path)
            raise ProviderError(ErrorCode.GENERATION_FAILED, "downloaded video is not decodable", transient=True,
                                message="The video provider returned a file that can't be played. Please try again.")
        log.info("pollinations video ok (%s bytes, %.1fs)", size, info.duration or 0)
        return ProviderResult(path=(path, EXT.get(ctype, ".mp4"), ctype), duration_seconds=info.duration,
                              meta={"model": model or "default", "provider": "pollinations", "bytes": size, "clip_seconds": info.duration,
                                    "requested_seconds": requested, "provider_seconds": provider_seconds,
                                    **({"dropped_parameters": dropped} if dropped else {}),
                                    **({"notes": [f"This model makes {provider_seconds}-second clips, the closest to the {requested} seconds you chose."]} if provider_seconds and provider_seconds != requested else {})})
