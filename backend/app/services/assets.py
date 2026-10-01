"""Generated assets: creation from a provider result, versioning, editing, duplication, deletion and downloads.
Text assets (story/script/lyrics) are plain editable text; audio assets (music/voice) are files in storage."""
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import AppError
from .. import media
from ..generators import BY_ID
from ..models import GeneratedAsset, GenerationJob, User
from ..providers import ErrorCode, ProviderError, ProviderResult
from ..storage import get_storage
from ..textparse import parse_scenes, parse_title, word_count
from . import generation

TEXT_TYPES = {"STORY", "SCRIPT", "LYRICS"}
MAX_TEXT = 200_000


def is_text(a: GeneratedAsset) -> bool:
    return a.text_content is not None and not a.file_path


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "asset"


def _title(job: GenerationJob, result: ProviderResult, asset_type: str) -> str:
    if result.title:
        return result.title[:200]
    o = job.options
    if asset_type == "MUSIC":
        label = " · ".join(x for x in (o.get("genre_custom") or o.get("genre"), o.get("mood")) if x)
        first_line = (job.original_prompt or "").strip().split("\n")[0]
        return (f"{label} theme" if label else first_line[:60] or "Music")
    if asset_type == "VOICE":
        return (job.refined_prompt or "Voice")[:50]
    if asset_type == "VIDEO":
        sentence = re.split(r"(?<=[.!?])\s", (job.original_prompt or "").strip())[0]
        return (sentence[:67] + "…" if len(sentence) > 70 else sentence) or "Video"
    if asset_type == "FACE":
        return "Face replacement"
    return (job.original_prompt or BY_ID[job.type].label)[:80]


def _lineage(db: Session, job: GenerationJob, asset_type: str) -> tuple[str | None, int]:
    """Regenerations join the lineage of what they regenerate and get the next version number."""
    lineage = job.input_meta.get("lineage_id") if job.input_meta else None
    if not lineage and job.parent_id:
        parent = db.scalars(select(GeneratedAsset).where(GeneratedAsset.job_id == job.parent_id)).first()
        lineage = (parent.lineage_id or parent.id) if parent and parent.type == asset_type else None
    if not lineage:
        return None, 1
    top = db.scalar(select(func.max(GeneratedAsset.version)).where(GeneratedAsset.lineage_id == lineage, GeneratedAsset.project_id == job.project_id))
    return (lineage, (top or 0) + 1) if top else (None, 1)


def create_from_result(db: Session, job: GenerationJob, result: ProviderResult) -> GeneratedAsset:
    asset_type = BY_ID[job.type].asset_type
    lineage, version = _lineage(db, job, asset_type)
    scene_id = (job.input_meta or {}).get("scene_id")
    meta = {**result.meta, **({"scene_id": scene_id} if scene_id else {}), "simulated": result.simulated, "options": {k: v for k, v in job.options.items() if k != "character_ids"},
            "original_prompt": job.original_prompt}
    text = result.text
    asset = GeneratedAsset(project_id=job.project_id, user_id=job.user_id, job_id=job.id, type=asset_type,
                           title=_title(job, result, asset_type), prompt=job.refined_prompt, provider=job.provider, meta=meta,
                           text_content=text, version=version, lineage_id=lineage, language=result.language or "",
                           duration_seconds=result.duration_seconds, status="SIMULATED" if result.simulated else "READY")
    if text is not None and not result.file:
        asset.format = "txt"
        _annotate_text(asset)
    if result.file:
        data, ext, mime = result.file
        asset.file_path = get_storage().save("generated", job.project_id, f"{job.type}{ext}", data)
        asset.format, asset.mime_type = ext.lstrip("."), mime
        asset.meta = {**asset.meta, "bytes": len(data)}
    if result.path:
        _store_downloaded(asset, job, result)
    db.add(asset)
    db.flush()
    if not asset.lineage_id:
        asset.lineage_id = asset.id
    db.commit()
    if scene_id and asset_type == "VIDEO":
        from . import scenes
        scenes.attach_video(db, scene_id, asset)
    return asset


MIME_BY_EXT = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


def _store_downloaded(asset: GeneratedAsset, job: GenerationJob, result: ProviderResult) -> None:
    """Large provider outputs arrive as a temp file: inspect it, make a thumbnail, then move it into storage (never read into memory)."""
    import os
    import tempfile
    tmp, ext, _ctype = result.path
    thumb_tmp = tempfile.mktemp(prefix="dc_thumb_", suffix=".jpg")
    try:
        info = media.probe(tmp)
        if info is None and media.ffmpeg_exe():
            raise ProviderError(ErrorCode.GENERATION_FAILED, "output is not decodable media", transient=True,
                                message="The provider returned a file that isn't valid media. Please try again.")
        storage = get_storage()
        thumb_key = None
        if info and media.make_thumbnail(tmp, thumb_tmp):
            thumb_key = storage.save_file("generated", job.project_id, "thumbnail.jpg", thumb_tmp)
        mime = MIME_BY_EXT.get(ext, result.path[2])
        asset.file_path = storage.save_file("generated", job.project_id, f"{job.type}{ext}", tmp)
        asset.format, asset.mime_type = ext.lstrip("."), mime
        requested = job.options.get("duration_seconds")
        asset.duration_seconds = (round(info.duration, 2) if info and info.duration else None) or (float(requested) if requested else None)
        asset.meta = {**asset.meta, **(result.meta or {}), "thumbnail": thumb_key,
                      "width": info.width if info else None, "height": info.height if info else None,
                      "aspect_ratio": (info.aspect_ratio if info else None) or job.options.get("aspect_ratio"),
                      "requested_duration": requested, "method": job.options.get("method")}
        src = next((r for r in job.reference_assets), None) if job.options.get("method") == "Image to Video" else job.options.get("source_asset_id")
        if src:
            asset.meta = {**asset.meta, "source_asset_id": src if isinstance(src, str) else None,
                          "face_asset_id": job.options.get("face_asset_id")}
    finally:
        for p in (tmp, thumb_tmp):
            if os.path.exists(p):
                os.unlink(p)


def _annotate_text(asset: GeneratedAsset) -> None:
    meta = dict(asset.meta or {})
    meta["word_count"] = word_count(asset.text_content)
    if asset.type == "SCRIPT":
        meta["scenes"] = [{"number": s["number"], "heading": s["heading"], "start": s["start"], "end": s["end"]}
                          for s in parse_scenes(asset.text_content)]
    asset.meta = meta


def get_owned(db: Session, user: User, asset_id: str) -> GeneratedAsset:
    from ..errors import NotFound
    a = db.get(GeneratedAsset, asset_id)
    if not a or a.user_id != user.id:
        raise NotFound("Asset not found.")
    return a


def versions(db: Session, a: GeneratedAsset) -> list[GeneratedAsset]:
    q = select(GeneratedAsset).where(GeneratedAsset.project_id == a.project_id, GeneratedAsset.lineage_id == (a.lineage_id or a.id))
    return list(db.scalars(q.order_by(GeneratedAsset.version)))


def update_text(db: Session, a: GeneratedAsset, text: str | None, title: str | None) -> GeneratedAsset:
    """Manual edits only change stored text; no AI call is made."""
    if text is not None:
        if not is_text(a):
            raise AppError("Only text assets can be edited.", 400, "not_editable")
        if len(text) > MAX_TEXT:
            raise AppError("This text is too long to save.", 422, "validation_error")
        a.text_content = text.replace("\r\n", "\n")
        _annotate_text(a)
        a.meta = {**a.meta, "edited": True}
        if a.type in TEXT_TYPES and (t := parse_title(text)) and title is None:
            a.title = t
    if title is not None:
        a.title = title.strip()[:200] or a.title
    a.updated_at = datetime.now(timezone.utc)
    db.commit()
    return a


def duplicate(db: Session, a: GeneratedAsset) -> GeneratedAsset:
    """A copy is an independent asset (its own lineage, version 1)."""
    copy = GeneratedAsset(project_id=a.project_id, user_id=a.user_id, type=a.type, title=f"{a.title} (copy)"[:200], prompt=a.prompt,
                          provider=a.provider, meta={**a.meta, "duplicated_from": a.id}, text_content=a.text_content, status=a.status,
                          format=a.format, language=a.language, mime_type=a.mime_type, duration_seconds=a.duration_seconds)
    if a.file_path:
        storage = get_storage()
        copy.file_path = storage.copy(a.file_path, "generated", a.project_id, f"{a.type.lower()}.{a.format or 'bin'}")
        if (a.meta or {}).get("thumbnail"):
            copy.meta = {**copy.meta, "thumbnail": storage.copy(a.meta["thumbnail"], "generated", a.project_id, "thumbnail.jpg")}
    db.add(copy)
    db.flush()
    copy.lineage_id = copy.id
    db.commit()
    return copy


def delete(db: Session, a: GeneratedAsset) -> None:
    if a.file_path:
        get_storage().delete(a.file_path)
    if (a.meta or {}).get("thumbnail"):
        get_storage().delete(a.meta["thumbnail"])
    db.delete(a)
    db.commit()


GENERATOR_BY_ASSET_TYPE = {"STORY": "story", "SCRIPT": "script", "LYRICS": "lyrics", "MUSIC": "music", "VOICE": "voice",
                           "VIDEO": "video", "FACE": "face_replacement"}


def regenerate(db: Session, user: User, a: GeneratedAsset) -> GenerationJob:
    """New job from the asset's saved request; its result becomes the next version of this asset (nothing is overwritten)."""
    generator = GENERATOR_BY_ASSET_TYPE.get(a.type)
    if not generator:
        raise AppError("This asset can't be regenerated yet.", 400, "not_regenerable")
    job = db.get(GenerationJob, a.job_id) if a.job_id else None
    meta = a.meta or {}
    return generation.submit(db, user, generator, meta.get("original_prompt", "") or (job.original_prompt if job else ""), a.prompt,
                             job.options if job else meta.get("options", {}), a.project_id, job.reference_assets if job else [],
                             parent_id=job.id if job else None, lineage_id=a.lineage_id or a.id)


def download(a: GeneratedAsset, fmt: str | None) -> tuple[bytes | str, str, str]:
    """(content or local file path, filename, mime). File assets return the path so large videos are streamed, not loaded."""
    base = f"{slug(a.title)}-{a.type.lower()}-v{a.version}"
    if is_text(a):
        fmt = (fmt or "txt").lower()
        if fmt not in ("txt", "md"):
            raise AppError("Unsupported download format.", 400, "bad_format")
        body = a.text_content if fmt == "txt" else f"# {a.title}\n\n{a.text_content}\n"
        return body.encode("utf-8"), f"{base}.{fmt}", "text/plain; charset=utf-8" if fmt == "txt" else "text/markdown; charset=utf-8"
    if not a.file_path or not get_storage().exists(a.file_path):
        raise AppError("This asset has no file to download.", 404, "no_file")
    path = get_storage().local_path(a.file_path)
    return (str(path) if path else get_storage().read(a.file_path)), f"{base}.{a.format or 'bin'}", a.mime_type or "application/octet-stream"
