from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import owned_project
from ..errors import NotFound
from ..models import Character, Project
from ..schemas import CharacterIn, CharacterOut
from ..services.projects import character_out
from ..storage import get_storage
from ..uploads import read_validated_image

router = APIRouter(prefix="/api/projects/{project_id}/characters", tags=["characters"])


def _get(db: Session, p: Project, character_id: str) -> Character:
    c = db.get(Character, character_id)
    if not c or c.project_id != p.id:
        raise NotFound("Character not found.")
    return c


def _touch(p: Project):
    from datetime import datetime, timezone
    p.updated_at = datetime.now(timezone.utc)


@router.get("", response_model=list[CharacterOut])
def list_characters(p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    rows = db.scalars(select(Character).where(Character.project_id == p.id).order_by(Character.created_at)).all()
    return [character_out(c) for c in rows]


@router.post("", response_model=CharacterOut, status_code=201)
def create_character(body: CharacterIn, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    c = Character(project_id=p.id, **body.model_dump())
    db.add(c)
    _touch(p)
    db.commit()
    return character_out(c)


@router.put("/{character_id}", response_model=CharacterOut)
def update_character(character_id: str, body: CharacterIn, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    c = _get(db, p, character_id)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    _touch(p)
    db.commit()
    return character_out(c)


@router.delete("/{character_id}", status_code=204)
def delete_character(character_id: str, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    c = _get(db, p, character_id)
    if c.reference_image_path:
        get_storage().delete(c.reference_image_path)
    db.delete(c)
    _touch(p)
    db.commit()


@router.post("/{character_id}/image", response_model=CharacterOut)
async def upload_character_image(character_id: str, file: UploadFile = File(...),
                                 p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    c = _get(db, p, character_id)
    data, _mime, ext = await read_validated_image(file)
    storage = get_storage()
    new_key = storage.save("uploads", p.id, f"character{ext}", data)
    if c.reference_image_path:
        storage.delete(c.reference_image_path)
    c.reference_image_path = new_key
    _touch(p)
    db.commit()
    db.refresh(c)
    return character_out(c)


@router.delete("/{character_id}/image", response_model=CharacterOut)
def delete_character_image(character_id: str, p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    c = _get(db, p, character_id)
    if c.reference_image_path:
        get_storage().delete(c.reference_image_path)
        c.reference_image_path = None
        db.commit()
    return character_out(c)
