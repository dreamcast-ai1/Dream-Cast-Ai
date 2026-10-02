from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, owned_project
from ..generators import ASPECT_RATIOS
from ..models import Project, User
from ..schemas import GenerationOut
from ..services import movie, scenes
from ..services.asset_views import asset_out

router = APIRouter(prefix="/api/projects/{project_id}", tags=["scenes"])


class SceneIn(BaseModel):
    number: int | None = Field(default=None, ge=1, le=999)
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    script: str = Field(default="", max_length=20000)
    character_ids: list[str] = Field(default_factory=list, max_length=20)
    visual_prompt: str = Field(default="", max_length=4000)
    duration_seconds: float | None = None


class ScenePatch(BaseModel):
    number: int | None = Field(default=None, ge=1, le=999)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    script: str | None = Field(default=None, max_length=20000)
    character_ids: list[str] | None = Field(default=None, max_length=20)
    visual_prompt: str | None = Field(default=None, max_length=4000)
    duration_seconds: float | None = None


class FromScriptIn(BaseModel):
    script_asset_id: str
    replace: bool = False


class GenerateVideoIn(BaseModel):
    aspect_ratio: str = "16:9"


def _with_notes(db: Session, scene, notes: list[str]) -> dict:
    return {**scenes.scene_out(db, scene), "notes": notes}


@router.get("/scenes")
def list_scenes(p: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return {"items": [scenes.scene_out(db, s) for s in scenes.list_scenes(db, p.id)]}


@router.post("/scenes", status_code=201)
def create_scene(body: SceneIn, p: Project = Depends(owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)):
    scene, notes = scenes.create(db, user, p, body.model_dump())
    return _with_notes(db, scene, notes)


@router.post("/scenes/from-script", status_code=201)
def scenes_from_script(body: FromScriptIn, p: Project = Depends(owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Turns each scene of a generated script into a movie scene (free: no AI call)."""
    scenes.import_from_script(db, user, p, body.script_asset_id, body.replace)
    return {"items": [scenes.scene_out(db, s) for s in scenes.list_scenes(db, p.id)]}


@router.patch("/scenes/{scene_id}")
def update_scene(scene_id: str, body: ScenePatch, p: Project = Depends(owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)):
    scene, notes = scenes.update(db, p, scenes.get_owned(db, user, p, scene_id), body.model_dump(exclude_unset=True))
    return _with_notes(db, scene, notes)


@router.delete("/scenes/{scene_id}", status_code=204)
def delete_scene(scene_id: str, p: Project = Depends(owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)):
    scenes.delete(db, p, scenes.get_owned(db, user, p, scene_id))


@router.post("/scenes/{scene_id}/generate-video", response_model=GenerationOut, status_code=201)
def generate_scene_video(scene_id: str, body: GenerateVideoIn | None = None, p: Project = Depends(owned_project),
                         user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Uses one video generation from your allowance (refunded if the provider fails, like any video)."""
    ratio = body.aspect_ratio if body and body.aspect_ratio in ASPECT_RATIOS else "16:9"
    job = scenes.generate_video(db, user, p, scenes.get_owned(db, user, p, scene_id), ratio)
    return GenerationOut(job_id=job.id, status=job.status)


class NarrationIn(BaseModel):
    gender: str = Field(default="", max_length=10)
    emotion: str = Field(default="", max_length=20)


@router.post("/scenes/{scene_id}/generate-narration", response_model=GenerationOut, status_code=201)
def generate_scene_narration(scene_id: str, body: NarrationIn | None = None, p: Project = Depends(owned_project),
                             user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Speaks the scene's text with the voice provider. Uses one voice generation from your allowance (refunded if the provider fails)."""
    job = scenes.generate_narration(db, user, p, scenes.get_owned(db, user, p, scene_id), (body.gender if body else ""), (body.emotion if body else ""))
    return GenerationOut(job_id=job.id, status=job.status)


@router.get("/movie")
def movie_state(p: Project = Depends(owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)):
    state = movie.readiness(db, p)
    job = movie.active_job(db, p, user.id)
    final = movie.latest_movie(db, p)
    return {**state, "active_job": {"id": job.id, "status": job.status, "stage": job.stage} if job else None,
            "movie": asset_out(final) if final else None}


class AssembleIn(BaseModel):
    narration: bool = True                                   # mix each scene's narration (where one exists) over its clip
    music_asset_id: str | None = Field(default=None, max_length=40)     # optional soundtrack from this project, played quietly under everything


@router.post("/movie/assemble", response_model=GenerationOut, status_code=202)
def assemble(body: AssembleIn | None = None, p: Project = Depends(owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Queues the assembly and returns at once. Free of video allowance: it only joins clips (and narration/music) you already generated."""
    job = movie.start_assembly(db, user, p, narration=body.narration if body else True, music_asset_id=body.music_asset_id if body else None)
    return GenerationOut(job_id=job.id, status=job.status)
