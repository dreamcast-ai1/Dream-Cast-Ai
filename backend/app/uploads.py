"""Image upload validation: size limit + magic-byte sniffing (never trust the client's content-type)."""
from fastapi import UploadFile

from .config import get_settings
from .errors import AppError

_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
]


def sniff_image(data: bytes) -> tuple[str, str] | None:
    for sig, mime, ext in _SIGNATURES:
        if data.startswith(sig):
            return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


async def read_validated_image(file: UploadFile) -> tuple[bytes, str, str]:
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if not data:
        raise AppError("The uploaded file is empty.", 400, "empty_file")
    if len(data) > limit:
        raise AppError(f"Image is too large. Maximum size is {get_settings().max_upload_mb} MB.", 413, "file_too_large")
    sniffed = sniff_image(data)
    if not sniffed:
        raise AppError("Unsupported file type. Please upload a PNG, JPEG, WebP or GIF image.", 415, "bad_file_type")
    return data, sniffed[0], sniffed[1]
