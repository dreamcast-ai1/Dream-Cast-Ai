import hashlib
import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import media
from ..config import get_settings
from ..db import get_db
from ..deps import owned_project
from ..errors import AppError, NotFound
from ..generators import REFERENCE_TYPES
from ..models import Project, ReferenceAsset
from ..schemas import ReferenceOut
from ..security import sanitize_filename
from ..services.projects import reference_out
from ..storage import get_storage
from ..imagemeta import image_size
from ..uploads import read_validated_image

router = APIRouter(prefix="/api/projects/{project_id}/references", tags=["references"])


@router.get("", response_model=list[ReferenceOut])
def list_references(type: str | None = None, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    q = select(ReferenceAsset).where(ReferenceAsset.project_id == p.id).order_by(ReferenceAsset.created_at.desc())
    if type:
        q = q.where(ReferenceAsset.type == type.upper())
    return [reference_out(r) for r in db.scalars(q).all()]


def _existing(db: Session, p: Project, sha: str) -> ReferenceAsset | None:
    return next((r for r in db.scalars(select(ReferenceAsset).where(ReferenceAsset.project_id == p.id))
                 if (r.meta or {}).get("sha256") == sha), None)


@router.post("", response_model=ReferenceOut, status_code=201)
async def upload_reference(response: Response, file: UploadFile = File(...), type: str = Form("OTHER"), name: str = Form(""),
                           p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Image upload. Uploading the same file twice to a project returns the existing reference (200) instead of storing a duplicate."""
    type = type.upper()
    if type not in REFERENCE_TYPES:
        raise AppError("Invalid reference type.", 422, "validation_error")
    data, mime, ext = await read_validated_image(file)
    size = image_size(data)
    if type == "FACE" and (not size or min(size) < 64):
        raise AppError("The face image is too small. Use an image at least 64 pixels wide and tall.", 422, "image_too_small")
    if size and max(size) > 10000:
        raise AppError("This image is too large in pixels (maximum 10000 on the longest side).", 422, "image_too_large")
    sha = hashlib.sha256(data).hexdigest()
    if dup := _existing(db, p, sha):
        response.status_code = 200
        return reference_out(dup)
    original = sanitize_filename(file.filename or f"reference{ext}")
    key = get_storage().save("uploads", p.id, original, data)
    ref = ReferenceAsset(project_id=p.id, user_id=p.user_id, type=type, name=(name.strip() or original)[:200],
                         file_path=key, original_filename=original, mime_type=mime, size_bytes=len(data),
                         meta={"sha256": sha, "width": size[0] if size else None, "height": size[1] if size else None})
    db.add(ref)
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    return reference_out(ref)


MAX_VIDEO_SECONDS = 120


def _sniff_video(head: bytes) -> tuple[str, str] | None:
    if head[4:8] == b"ftyp":
        return ("video/quicktime", ".mov") if head[8:12] == b"qt  " else ("video/mp4", ".mp4")
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm", ".webm"
    return None


@router.post("/video", response_model=ReferenceOut, status_code=201)
async def upload_video(response: Response, file: UploadFile = File(...), type: str = Form("SOURCE"), name: str = Form(""),
                       p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Video source upload for face replacement. Streamed to a temp file in chunks (never held in memory), validated by content
    (not file name or the browser's content-type), then moved into storage."""
    type = type.upper()
    if type not in REFERENCE_TYPES:
        raise AppError("Invalid reference type.", 422, "validation_error")
    limit, hasher, size = get_settings().max_video_upload_bytes, hashlib.sha256(), 0
    tmp = tempfile.NamedTemporaryFile(prefix="dc_up_", delete=False)
    try:
        head = b""
        while chunk := await file.read(1024 * 1024):
            if len(head) < 16:
                head += chunk[: 16 - len(head)]
            size += len(chunk)
            if size > limit:
                raise AppError(f"Video is too large. Maximum size is {get_settings().max_video_upload_mb} MB.", 413, "file_too_large")
            hasher.update(chunk)
            tmp.write(chunk)
        tmp.close()
        if not size:
            raise AppError("The uploaded file is empty.", 400, "empty_file")
        sniffed = _sniff_video(head)
        if not sniffed:
            raise AppError("Unsupported file type. Please upload an MP4, MOV or WebM video.", 415, "bad_file_type")
        info = media.probe(tmp.name)
        if info is None and media.ffmpeg_exe():
            raise AppError("This file could not be read as a video.", 422, "invalid_video")
        if info and info.duration and info.duration > MAX_VIDEO_SECONDS:
            raise AppError(f"Videos can be at most {MAX_VIDEO_SECONDS} seconds long.", 422, "video_too_long")
        sha = hasher.hexdigest()
        if dup := _existing(db, p, sha):
            response.status_code = 200
            return reference_out(dup)
        original = sanitize_filename(file.filename or f"video{sniffed[1]}")
        key = get_storage().save_file("uploads", p.id, original, tmp.name)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
    ref = ReferenceAsset(project_id=p.id, user_id=p.user_id, type=type, name=(name.strip() or original)[:200], file_path=key,
                         original_filename=original, mime_type=sniffed[0], size_bytes=size,
                         meta={"sha256": sha, "width": info.width if info else None, "height": info.height if info else None,
                               "duration_seconds": round(info.duration, 2) if info and info.duration else None})
    db.add(ref)
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    return reference_out(ref)


@router.delete("/{reference_id}", status_code=204)
def delete_reference(reference_id: str, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    ref = db.get(ReferenceAsset, reference_id)
    if not ref or ref.project_id != p.id:
        raise NotFound("Reference not found.")
    get_storage().delete(ref.file_path)
    if p.thumbnail_path == ref.file_path and ref.mime_type.startswith("image/"):
        p.thumbnail_path = None
    db.delete(ref)
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
