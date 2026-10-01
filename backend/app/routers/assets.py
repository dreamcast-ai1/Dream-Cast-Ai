from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..db import get_db
from .. import streaming
from ..deps import current_user
from ..generators import ASSET_TYPES
from ..models import GeneratedAsset, Project, User
from ..schemas import AssetDetail, AssetOut, AssetPatch, GenerationOut, LibraryItem
from ..errors import NotFound
from ..services import assets, scene_context
from ..services.asset_views import asset_detail, asset_out

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("", response_model=list[LibraryItem])
def library(type: str | None = None, project_id: str | None = None, movies: bool | None = None, limit: int = 40, offset: int = 0,
            user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The user's whole library, newest first, across all projects. `type` is a comma-separated list of asset types
    (VIDEO, IMAGE, STORY...); `movies=true` keeps only assembled movies, `movies=false` hides them. Only the caller's own assets are ever returned."""
    q = (select(GeneratedAsset, Project.title).join(Project, Project.id == GeneratedAsset.project_id)
         .where(GeneratedAsset.user_id == user.id, Project.user_id == user.id))
    if type:
        q = q.where(GeneratedAsset.type.in_([t.strip().upper() for t in type.split(",") if t.strip().upper() in ASSET_TYPES]))
    if project_id:
        q = q.where(GeneratedAsset.project_id == project_id)
    rows = db.execute(q.order_by(GeneratedAsset.created_at.desc())).all()
    items = []
    for a, title in rows:
        is_movie = bool((a.meta or {}).get("movie"))
        if movies is not None and movies != is_movie:
            continue
        items.append(LibraryItem(**asset_out(a).model_dump(), project_title=title, is_movie=is_movie))
    start = max(offset, 0)
    return items[start:start + min(max(limit, 1), 100)]


@router.get("/{asset_id}", response_model=AssetDetail)
def get_asset(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return asset_detail(db, assets.get_owned(db, user, asset_id))


@router.get("/{asset_id}/scenes/{number}")
def scene(asset_id: str, number: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One scene of a script as structured fields plus a draft video prompt and the project characters it mentions."""
    a = assets.get_owned(db, user, asset_id)
    if a.type != "SCRIPT" or not a.text_content:
        raise NotFound("Scene not found in this script.")
    return scene_context.scene_detail(db, a, number)


@router.get("/{asset_id}/sections")
def sections(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The parts of a story (setting, acts, ending) a video can be created from."""
    a = assets.get_owned(db, user, asset_id)
    if a.type != "STORY" or not a.text_content:
        raise NotFound("Story not found.")
    return {"sections": scene_context.story_sections(a)}


@router.put("/{asset_id}", response_model=AssetDetail)
def edit_asset(asset_id: str, body: AssetPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Saves manual edits to a text asset (or renames it). Never calls an AI provider."""
    a = assets.update_text(db, assets.get_owned(db, user, asset_id), body.text_content, body.title)
    return asset_detail(db, a)


@router.post("/{asset_id}/duplicate", response_model=AssetOut, status_code=201)
def duplicate_asset(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return asset_out(assets.duplicate(db, assets.get_owned(db, user, asset_id)))


@router.post("/{asset_id}/regenerate", response_model=GenerationOut, status_code=201)
def regenerate_asset(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = assets.regenerate(db, user, assets.get_owned(db, user, asset_id))
    return GenerationOut(job_id=job.id, status=job.status)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    assets.delete(db, assets.get_owned(db, user, asset_id))


@router.get("/{asset_id}/download")
def download_asset(request: Request, asset_id: str, format: str | None = Query(default=None), user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    data, filename, mime = assets.download(assets.get_owned(db, user, asset_id), format)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if isinstance(data, assets.StoredKey):
        return streaming.stream_key(request, data, {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}, filename, True)
    if isinstance(data, str):
        return FileResponse(data, media_type=mime, headers=headers)      # streamed from disk
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                                    "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
