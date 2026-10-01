"""Serving stored files from any Storage with HTTP Range support (so <video> can seek), without loading big files into memory.
Local storage keeps using FileResponse; object storage streams through the backend so ownership is always checked first."""
import re

from fastapi import Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from .errors import AppError
from .storage import get_storage
from .uploads import sniff_image

MIME_BY_EXT = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
               "flac": "audio/flac", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def mime_for(key: str, head: bytes | None = None) -> str:
    sniffed = sniff_image(head) if head else None
    return sniffed[0] if sniffed else MIME_BY_EXT.get(key.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(start, end) inclusive for a single valid range, None for "no range"; raises 416 for an unsatisfiable one."""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None                                    # malformed: ignore it and send the whole file, as servers commonly do
    if m.group(1) == "":                               # suffix range: the last N bytes
        n = int(m.group(2))
        start, end = max(size - n, 0), size - 1
    else:
        start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
    end = min(end, size - 1)
    if start >= size or start > end:
        raise AppError("Requested range not satisfiable.", 416, "bad_range")
    return start, end


def stream_key(request: Request, key: str, headers: dict, name: str | None = None, download: bool = False) -> Response:
    storage = get_storage()
    size = storage.size(key)
    mime = mime_for(key, storage.read_range(key, 0, min(31, size - 1)) if size else None)
    out = {**headers, "Accept-Ranges": "bytes"}
    if download:
        out["Content-Disposition"] = f'attachment; filename="{name or "download"}"'
    rng = parse_range(request.headers.get("range"), size) if size else None
    if rng is None:
        out["Content-Length"] = str(size)
        return StreamingResponse(storage.iter_range(key, 0, size - 1) if size else iter(()), media_type=mime, headers=out)
    start, end = rng
    out.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
    return StreamingResponse(storage.iter_range(key, start, end), status_code=206, media_type=mime, headers=out)


def signed_download(key: str, name: str | None) -> Response | None:
    """For object storage: send downloads straight from the bucket with a short-lived signed link (saves bandwidth on the backend)."""
    url = get_storage().generate_signed_url(key, filename=name)
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"}) if url else None
