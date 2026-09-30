"""fal.ai queue API client and the shared behaviour of fal-backed providers (video, face swap).
Flow: submit (returns request/status/response/cancel URLs) -> poll status -> fetch result JSON -> stream the output file to
disk. The provider's own URLs are stored (as JSON in the job's external id) so a restarted worker can resume polling
instead of submitting (and paying for) the job again."""
import base64
import json
import logging
import tempfile
from abc import abstractmethod
from urllib.parse import urlparse

import httpx

from ..storage import get_storage
from .base import ErrorCode, GenerationRequest, Provider, ProviderError, ProviderJobStatus, ProviderResult
from .http_util import http_client, network_error, raise_for_provider_status

log = logging.getLogger("dreamcast.fal")
QUEUE_URL = "https://queue.fal.run"
EXT_BY_TYPE = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "image/png": ".png", "image/jpeg": ".jpg",
               "image/webp": ".webp"}
MAX_STATUS_BLIPS = 6          # consecutive transient polling errors tolerated before giving up (never resubmit on a blip)


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class FalQueueProvider(Provider):
    supports_cancel = True
    resumable = True
    what = "video"            # used in user-facing messages

    def __init__(self):
        self._blips: dict[str, int] = {}

    # --- configuration supplied by subclasses (kept out of this class so each reads its own settings)
    @property
    @abstractmethod
    def api_key(self) -> str: ...

    @property
    @abstractmethod
    def base_url(self) -> str: ...

    @property
    @abstractmethod
    def timeout(self) -> float: ...

    @property
    @abstractmethod
    def max_download_bytes(self) -> int: ...

    @abstractmethod
    def build_request(self, request: GenerationRequest) -> tuple[str, dict]:
        """Returns (model id, JSON input)."""

    @abstractmethod
    def output_url(self, result: dict) -> str | None: ...

    # --- helpers
    def _headers(self) -> dict:
        return {"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"}

    def _endpoint(self) -> str:
        return (self.base_url or QUEUE_URL).rstrip("/")

    def _check_url(self, url: str) -> str:
        """Provider-supplied URLs are only followed over https (http allowed solely when a custom base URL is configured for
        local development), so a malicious response can't point the server at arbitrary schemes."""
        u = urlparse(url)
        if u.scheme == "https" or (u.scheme == "http" and self.base_url):
            return url
        raise ProviderError(ErrorCode.GENERATION_FAILED, f"refusing to follow non-https url ({u.scheme})",
                            message=f"The {self.what} provider returned an unusable link.")

    def _raise(self, res: httpx.Response) -> None:
        if res.status_code == 403 and "balance" in res.text.lower():
            raise ProviderError(ErrorCode.QUOTA_EXCEEDED, "HTTP 403 (balance)",
                                message=f"The {self.what} provider account has no remaining credit. An administrator needs to top it up.")
        if res.status_code == 422:
            raise ProviderError(ErrorCode.INVALID_REQUEST, f"HTTP 422: {res.text[:300]}",
                                message=f"The {self.what} provider rejected this request. Try a different prompt or options.")
        raise_for_provider_status(res, self.what)

    # --- Provider interface
    def generate(self, request: GenerationRequest) -> str:
        model, payload = self.build_request(request)
        try:
            with http_client(self.timeout) as client:
                res = client.post(f"{self._endpoint()}/{model}", json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise network_error(e, self.what)
        self._raise(res)
        body = res.json()
        if not body.get("request_id") or not body.get("status_url") or not body.get("response_url"):
            raise ProviderError(ErrorCode.GENERATION_FAILED, "submit response missing urls", transient=True,
                                message=f"The {self.what} provider didn't accept the job. Please try again.")
        return json.dumps({"r": body["request_id"], "s": body["status_url"], "u": body["response_url"], "c": body.get("cancel_url", "")})

    def get_status(self, external_id: str) -> ProviderJobStatus:
        ref = json.loads(external_id)
        try:
            with http_client(self.timeout) as client:
                res = client.get(self._check_url(ref["s"]), headers=self._headers())
            if res.status_code >= 500 or res.status_code == 429:
                raise httpx.HTTPError(f"status {res.status_code}")
            self._raise(res)
            self._blips.pop(ref["r"], None)
        except httpx.HTTPError as e:
            n = self._blips[ref["r"]] = self._blips.get(ref["r"], 0) + 1
            if n >= MAX_STATUS_BLIPS:
                raise network_error(e, self.what)
            log.warning("status poll blip %s/%s for %s", n, MAX_STATUS_BLIPS, ref["r"])
            return ProviderJobStatus("PROCESSING")
        state = res.json().get("status", "")
        if state == "COMPLETED":
            return ProviderJobStatus("COMPLETED")           # output is fetched by download_result
        return ProviderJobStatus("PROCESSING", stage="GENERATING")   # IN_QUEUE / IN_PROGRESS: no real percentage is available

    def download_result(self, external_id: str, request: GenerationRequest) -> ProviderResult:
        ref = json.loads(external_id)
        try:
            with http_client(self.timeout) as client:
                res = client.get(self._check_url(ref["u"]), headers=self._headers())
        except httpx.HTTPError as e:
            raise network_error(e, self.what)
        if res.status_code == 422:      # the queued job itself failed inside the model
            raise ProviderError(ErrorCode.GENERATION_FAILED, f"provider job failed: {res.text[:300]}",
                                message=f"The {self.what} provider couldn't complete this request. Try changing the prompt or options.")
        if res.status_code >= 400:
            self._raise(res)
        url = self.output_url(res.json())
        if not url:
            raise ProviderError(ErrorCode.GENERATION_FAILED, "no output url in result",
                                message=f"The {self.what} provider finished but returned no result.")
        return self._download(self._check_url(url), request, ref["r"])

    def _download(self, url: str, request: GenerationRequest, request_id: str) -> ProviderResult:
        tmp = tempfile.NamedTemporaryFile(prefix="dc_dl_", delete=False)
        size = 0
        try:
            with http_client(self.timeout) as client, client.stream("GET", url) as res:
                if res.status_code >= 400:
                    raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"download HTTP {res.status_code}", transient=True,
                                        message=f"The {self.what} was created but could not be downloaded. Please try again.")
                ctype = res.headers.get("content-type", "").split(";")[0].strip().lower()
                for chunk in res.iter_bytes(1024 * 256):
                    size += len(chunk)
                    if size > self.max_download_bytes:
                        raise ProviderError(ErrorCode.STORAGE_ERROR, "download too large",
                                            message=f"The generated {self.what} is larger than this server allows.")
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
        ext = EXT_BY_TYPE.get(ctype) or (".mp4" if self.what == "video" else ".png")
        if not size:
            _rm(tmp.name)
            raise ProviderError(ErrorCode.GENERATION_FAILED, "empty download", transient=True, message=f"The {self.what} provider returned an empty file.")
        return ProviderResult(path=(tmp.name, ext, ctype or "application/octet-stream"), meta={"provider_job_id": request_id, "bytes": size})

    def cancel(self, external_id: str) -> bool:
        """True only if the provider confirmed the cancellation (fal can only cancel jobs still waiting in its queue)."""
        try:
            ref = json.loads(external_id)
            if not ref.get("c"):
                return False
            with http_client(self.timeout) as client:
                res = client.put(self._check_url(ref["c"]), headers=self._headers())
            return res.status_code in (200, 202)
        except (httpx.HTTPError, ProviderError, ValueError):
            return False


def _rm(path: str) -> None:
    import os
    try:
        os.unlink(path)
    except OSError:
        pass


def read_reference(ref: dict) -> bytes:
    return get_storage().read(ref["path"])
