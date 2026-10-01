from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Character, GeneratedAsset, Project, ReferenceAsset, Scene
from ..schemas import CharacterOut, ProjectDetail, ProjectOut, ReferenceOut
from ..storage import get_storage


def project_out(p: Project) -> ProjectOut:
    out = ProjectOut.model_validate(p)
    out.style = (p.meta or {}).get("style", "")
    out.thumbnail_url = f"/api/files/project/{p.id}?v={int(p.updated_at.timestamp())}" if p.thumbnail_path else None
    return out


def project_detail(db: Session, p: Project) -> ProjectDetail:
    counts = {"characters": db.scalar(select(func.count()).select_from(Character).where(Character.project_id == p.id)) or 0,
              "references": db.scalar(select(func.count()).select_from(ReferenceAsset).where(ReferenceAsset.project_id == p.id)) or 0,
              "scenes": db.scalar(select(func.count()).select_from(Scene).where(Scene.project_id == p.id)) or 0}
    rows = db.execute(select(GeneratedAsset.type, func.count()).where(GeneratedAsset.project_id == p.id)
                      .group_by(GeneratedAsset.type)).all()
    counts.update({t.lower(): n for t, n in rows})
    return ProjectDetail(**project_out(p).model_dump(), counts=counts)


def character_out(c: Character) -> CharacterOut:
    out = CharacterOut.model_validate(c)
    out.image_url = f"/api/files/character/{c.id}?v={int(c.updated_at.timestamp())}" if c.reference_image_path else None
    return out


def reference_out(r: ReferenceAsset) -> ReferenceOut:
    out = ReferenceOut.model_validate({**{k: getattr(r, k) for k in
                                          ("id", "project_id", "type", "name", "original_filename", "mime_type",
                                           "size_bytes", "created_at")}, "url": f"/api/files/reference/{r.id}",
                                                 "width": (r.meta or {}).get("width"), "height": (r.meta or {}).get("height"),
                                                 "duration_seconds": (r.meta or {}).get("duration_seconds")})
    return out


def delete_project(db: Session, p: Project) -> None:
    storage = get_storage()
    for area in ("projects", "uploads", "generated"):
        storage.delete_prefix(f"{area}/{p.id}")
    db.delete(p)
    db.commit()
