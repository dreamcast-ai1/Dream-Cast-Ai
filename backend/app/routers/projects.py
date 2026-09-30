from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, owned_project
from ..errors import NotFound
from ..generators import ASSET_TYPES
from ..models import GeneratedAsset, Project, ReferenceAsset, User
from ..schemas import AssetOut, ProjectDetail, ProjectIn, ProjectOut, ProjectPatch
from ..services import projects as svc
from ..services.asset_views import asset_out

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(limit: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Project).where(Project.user_id == user.id)
                      .order_by(Project.updated_at.desc()).limit(min(max(limit, 1), 200))).all()
    return [svc.project_out(p) for p in rows]


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = body.model_dump()
    style = data.pop("style", "")
    p = Project(user_id=user.id, meta={"style": style} if style else {}, **data)
    db.add(p)
    db.commit()
    return svc.project_detail(db, p)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return svc.project_detail(db, p)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(body: ProjectPatch, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "style":
            p.meta = {**(p.meta or {}), "style": v or ""}
        elif v is not None:
            setattr(p, k, v)
    db.commit()
    return svc.project_detail(db, p)


@router.delete("/{project_id}", status_code=204)
def delete_project(p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    svc.delete_project(db, p)


class PosterIn(BaseModel):
    reference_id: str | None = None


@router.put("/{project_id}/poster", response_model=ProjectDetail)
def set_poster(body: PosterIn, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    """Use one of the project's reference images as its poster (or clear it with null)."""
    if body.reference_id is None:
        p.thumbnail_path = None
    else:
        ref = db.get(ReferenceAsset, body.reference_id)
        if not ref or ref.project_id != p.id or not ref.mime_type.startswith("image/"):
            raise NotFound("Reference not found.")
        p.thumbnail_path = ref.file_path
    db.commit()
    return svc.project_detail(db, p)


@router.get("/{project_id}/assets", response_model=list[AssetOut])
def list_assets(type: str | None = None, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    """type: one asset type or a comma-separated list (STORY,SCRIPT,...). Newest first; every version is listed."""
    q = select(GeneratedAsset).where(GeneratedAsset.project_id == p.id).order_by(GeneratedAsset.created_at.desc())
    if type:
        types = [t.strip().upper() for t in type.split(",") if t.strip().upper() in ASSET_TYPES]
        if not types:
            return []
        q = q.where(GeneratedAsset.type.in_(types))
    return [asset_out(a) for a in db.scalars(q).all()]
