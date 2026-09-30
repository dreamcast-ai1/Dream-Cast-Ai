"""Authenticated file serving. Files are addressed by DB id (never by client-supplied path).

  GET  /api/files/{kind}/{id}        bearer-authenticated; streamed from disk (images, audio, small files)
  GET  /api/files/thumbnail/{id}     bearer-authenticated; small JPEG for a video/face asset
  POST /api/media/stream-url         bearer-authenticated; returns a short-lived signed URL for <video>/<audio> playback and downloads
  GET  /api/media/{token}            the signed URL: supports HTTP Range so the browser can seek without downloading the whole file
"""
import jwt
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..errors import NotFound, Unauthorized
from ..models import Character, GeneratedAsset, Project, ReferenceAsset, User
from ..security import create_token, decode_local_token
from ..services.assets import slug
from ..storage import get_storage
from ..uploads import sniff_image

router = APIRouter(prefix="/api", tags=["files"])

_MIME_BY_EXT = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
                "flac": "audio/flac", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
MEDIA_TOKEN_MINUTES = 30


def _item(db: Session, kind: str, item_id: str):
    if kind == "project":
        return db.get(Project, item_id)
    if kind == "reference":
        return db.get(ReferenceAsset, item_id)
    if kind == "character":
        return db.get(Character, item_id)
    if kind in ("asset", "thumbnail"):
        return db.get(GeneratedAsset, item_id)
    return None


def _owner_id(kind: str, item) -> str | None:
    if item is None:
        return None
    return item.project.user_id if kind == "character" else item.user_id


def _key(kind: str, item) -> str | None:
    return {"project": lambda i: i.thumbnail_path, "reference": lambda i: i.file_path, "character": lambda i: i.reference_image_path,
            "asset": lambda i: i.file_path, "thumbnail": lambda i: (i.meta or {}).get("thumbnail")}[kind](item)


def _resolve(db: Session, user: User, kind: str, item_id: str) -> tuple[str, object]:
    item = _item(db, kind, item_id)
    key = _key(kind, item) if item is not None else None
    if item is None or _owner_id(kind, item) != user.id or not key or not get_storage().exists(key):
        raise NotFound("File not found.")
    return key, item


def _serve(key: str, name: str | None = None, download: bool = False) -> Response:
    storage = get_storage()
    ext = key.rsplit(".", 1)[-1].lower()
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    path = storage.local_path(key)
    if path is not None:
        mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
        return FileResponse(path, media_type=mime, headers=headers, filename=name if download else None,
                            content_disposition_type="attachment" if download else "inline")
    data = storage.read(key)
    sniffed = sniff_image(data)
    return Response(data, media_type=sniffed[0] if sniffed else _MIME_BY_EXT.get(ext, "application/octet-stream"), headers=headers)


@router.get("/files/{kind}/{item_id}")
def get_file(kind: str, item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if kind not in ("project", "reference", "character", "asset", "thumbnail"):
        raise NotFound("File not found.")
    key, _ = _resolve(db, user, kind, item_id)
    return _serve(key)


class StreamIn(BaseModel):
    kind: str
    id: str


@router.post("/media/stream-url")
def stream_url(body: StreamIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Signed, short-lived URL for native <video>/<audio> playback (Range requests) and large downloads. Issued only after the
    ownership check; the token is bound to one file and one user."""
    if body.kind not in ("asset", "reference"):
        raise NotFound("File not found.")
    _resolve(db, user, body.kind, body.id)
    token = create_token(f"{body.kind}:{body.id}", "media", minutes=MEDIA_TOKEN_MINUTES, extra={"u": user.id})
    return {"url": f"/api/media/{token}", "expires_in": MEDIA_TOKEN_MINUTES * 60}


@router.get("/media/{token}")
def media(token: str, download: bool = False, db: Session = Depends(get_db)):
    try:
        claims = decode_local_token(token, "media")
        kind, item_id = claims["sub"].split(":", 1)
    except (jwt.PyJWTError, KeyError, ValueError):
        raise Unauthorized("This link has expired. Reload the page to get a new one.")
    item = _item(db, kind, item_id)
    owner = db.get(User, claims.get("u"))
    key = _key(kind, item) if item is not None else None
    if item is None or not owner or not owner.is_active or _owner_id(kind, item) != owner.id or not key or not get_storage().exists(key):
        raise NotFound("File not found.")
    name = f"{slug(getattr(item, 'title', None) or getattr(item, 'name', 'file'))}.{key.rsplit('.', 1)[-1]}"
    return _serve(key, name, download)
