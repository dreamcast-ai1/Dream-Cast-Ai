"""Movie scenes. A scene is just planning data (title, script, visual prompt...): creating or editing one is free.
Generating a scene's clip goes through the normal video pipeline, so it uses (and is limited by) the video allowance."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError, NotFound
from ..generators import ACTIVE_STATUSES
from ..models import Character, GeneratedAsset, GenerationJob, Project, Scene, User
from ..storage import get_storage
from . import generation, scene_context
from .generation_schema import _resolve_duration

MAX_SCENES = 60


def list_scenes(db: Session, project_id: str) -> list[Scene]:
    return list(db.scalars(select(Scene).where(Scene.project_id == project_id).order_by(Scene.number, Scene.created_at)))


def get_owned(db: Session, user: User, project: Project, scene_id: str) -> Scene:
    s = db.get(Scene, scene_id)
    if not s or s.user_id != user.id or s.project_id != project.id:
        raise NotFound("Scene not found.")
    return s


def clip_asset(db: Session, scene: Scene) -> GeneratedAsset | None:
    """The scene's current video, only if it still exists with its file."""
    a = db.get(GeneratedAsset, scene.video_asset_id) if scene.video_asset_id else None
    if a and a.project_id == scene.project_id and a.type == "VIDEO" and a.file_path and get_storage().exists(a.file_path):
        return a
    return None


def status_of(db: Session, scene: Scene) -> str:
    job = db.get(GenerationJob, scene.last_job_id) if scene.last_job_id else None
    if job and job.status in ACTIVE_STATUSES:
        return "GENERATING"
    if clip_asset(db, scene):
        return "READY"
    return "FAILED" if job and job.status == "FAILED" else "DRAFT"


def scene_out(db: Session, scene: Scene) -> dict:
    job = db.get(GenerationJob, scene.last_job_id) if scene.last_job_id else None
    clip = clip_asset(db, scene)
    nar = narration_asset(db, scene)
    njob = db.get(GenerationJob, scene.narration_job_id) if scene.narration_job_id else None
    names = [c.name for c in db.scalars(select(Character).where(Character.id.in_(scene.character_ids or []), Character.project_id == scene.project_id))]
    videos = [a for a in db.scalars(select(GeneratedAsset).where(GeneratedAsset.project_id == scene.project_id, GeneratedAsset.type == "VIDEO")
                                    .order_by(GeneratedAsset.created_at.desc())) if (a.meta or {}).get("scene_id") == scene.id and a.file_path]
    return {"id": scene.id, "project_id": scene.project_id, "number": scene.number, "title": scene.title, "description": scene.description,
            "script": scene.script, "character_ids": scene.character_ids or [], "characters": names, "visual_prompt": scene.visual_prompt,
            "duration_seconds": scene.duration_seconds, "status": status_of(db, scene),
            "video": {"asset_id": clip.id, "url": f"/api/files/asset/{clip.id}", "thumbnail_url": f"/api/files/thumbnail/{clip.id}" if (clip.meta or {}).get("thumbnail") else None,
                      "duration_seconds": clip.duration_seconds} if clip else None,
            "assets": [{"id": a.id, "version": a.version, "created_at": a.created_at, "selected": bool(clip and a.id == clip.id)} for a in videos],
            "job": {"id": job.id, "status": job.status, "stage": job.stage, "error_message": job.error_message} if job else None,
            "narration": ({"asset_id": nar.id, "url": f"/api/files/asset/{nar.id}", "duration_seconds": nar.duration_seconds} if nar else None),
            "narration_job": ({"id": njob.id, "status": njob.status, "error_message": njob.error_message} if njob else None),
            "script_asset_id": scene.script_asset_id}


def _duration(value, notes: list[str]) -> int:
    """Clip length: 10, 20 or 30 s. Anything longer is capped to 30 (and we say so); this is the same rule as the Video generator."""
    return _resolve_duration("video", value, "", notes, strict=False)


def _valid_characters(db: Session, project: Project, ids: list[str] | None) -> list[str]:
    ids = list(dict.fromkeys(ids or []))
    if not ids:
        return []
    found = {c.id for c in db.scalars(select(Character).where(Character.id.in_(ids), Character.project_id == project.id))}
    if found != set(ids):
        raise AppError("One of the characters was not found in this project.", 422, "validation_error")
    return ids


def create(db: Session, user: User, project: Project, data: dict) -> tuple[Scene, list[str]]:
    existing = list_scenes(db, project.id)
    if len(existing) >= MAX_SCENES:
        raise AppError(f"A movie can have up to {MAX_SCENES} scenes.", 422, "too_many_scenes")
    notes: list[str] = []
    number = data.get("number") or (max((s.number for s in existing), default=0) + 1)
    if any(s.number == number for s in existing):
        raise AppError(f"Scene {number:02d} already exists.", 409, "scene_exists")
    scene = Scene(project_id=project.id, user_id=user.id, number=number, title=(data.get("title") or "").strip(),
                  description=(data.get("description") or "").strip(), script=data.get("script") or "",
                  visual_prompt=(data.get("visual_prompt") or "").strip(), character_ids=_valid_characters(db, project, data.get("character_ids")),
                  duration_seconds=_duration(data.get("duration_seconds"), notes), script_asset_id=data.get("script_asset_id"))
    db.add(scene)
    db.commit()
    return scene, notes


def update(db: Session, project: Project, scene: Scene, data: dict) -> tuple[Scene, list[str]]:
    notes: list[str] = []
    for key in ("title", "description", "visual_prompt"):
        if data.get(key) is not None:
            setattr(scene, key, data[key].strip())
    if data.get("script") is not None:
        scene.script = data["script"]
    if data.get("character_ids") is not None:
        scene.character_ids = _valid_characters(db, project, data["character_ids"])
    if data.get("duration_seconds") is not None:
        scene.duration_seconds = _duration(data["duration_seconds"], notes)
    if data.get("number") is not None and data["number"] != scene.number:
        other = next((s for s in list_scenes(db, project.id) if s.number == data["number"] and s.id != scene.id), None)
        if other:                                   # moving onto an occupied number swaps the two scenes
            other.number = scene.number
        scene.number = data["number"]
    db.commit()
    return scene, notes


def delete(db: Session, project: Project, scene: Scene) -> None:
    job = db.get(GenerationJob, scene.last_job_id) if scene.last_job_id else None
    if job and job.status in ACTIVE_STATUSES:
        raise AppError("Cancel this scene's video before deleting the scene.", 409, "scene_busy")
    db.delete(scene)
    db.flush()
    for i, s in enumerate(list_scenes(db, project.id), start=1):      # keep numbering 1..n
        s.number = i
    db.commit()


def import_from_script(db: Session, user: User, project: Project, script_asset_id: str, replace: bool) -> list[Scene]:
    script = db.get(GeneratedAsset, script_asset_id)
    if not script or script.user_id != user.id or script.project_id != project.id or script.type != "SCRIPT" or not script.text_content:
        raise NotFound("Script not found in this project.")
    from ..textparse import parse_scenes, scene_text
    parsed = parse_scenes(script.text_content)
    if not parsed:
        raise AppError("No scenes were found in this script.", 422, "no_scenes")
    existing = list_scenes(db, project.id)
    if existing and not replace:
        raise AppError("This movie already has scenes. Choose to replace them, or add scenes one at a time.", 409, "scenes_exist")
    if any((a := db.get(GenerationJob, s.last_job_id)) and a.status in ACTIVE_STATUSES for s in existing if s.last_job_id):
        raise AppError("Wait for running scene videos to finish before replacing the scenes.", 409, "scene_busy")
    for s in existing:
        db.delete(s)
    db.flush()
    out = []
    for i, sc in enumerate(parsed[:MAX_SCENES], start=1):
        d = scene_context.scene_detail(db, script, sc["number"])
        out.append(Scene(project_id=project.id, user_id=user.id, number=i, title=(d.get("heading") or sc["heading"] or f"Scene {i}")[:200],
                         description=(d.get("action") or "")[:1000], script=scene_text(script.text_content, sc),
                         visual_prompt=d["video_prompt"], character_ids=d["character_ids"], duration_seconds=10, script_asset_id=script.id))
    db.add_all(out)
    db.commit()
    return out


def generate_video(db: Session, user: User, project: Project, scene: Scene, aspect_ratio: str = "16:9") -> GenerationJob:
    """Queues ONE normal video job for the scene. All the usual rules apply: video allowance (reserved here, refunded on failure
    exactly as for any video), the 30 s clip cap, plan entitlements and provider availability."""
    prev = db.get(GenerationJob, scene.last_job_id) if scene.last_job_id else None
    if prev and prev.status in ACTIVE_STATUSES:
        raise AppError("This scene's video is already being generated.", 409, "scene_busy")
    prompt = scene.visual_prompt or " ".join(x for x in (scene.title, scene.description) if x) or scene.script[:700]
    if not prompt.strip():
        raise AppError("Add a description or a visual prompt to this scene first.", 422, "validation_error")
    job = generation.submit(db, user, "video", prompt, prompt, {"method": "Text to Video", "duration_seconds": scene.duration_seconds,
                                                                 "aspect_ratio": aspect_ratio, "character_ids": scene.character_ids or []},
                            project.id, [], parent_id=prev.id if prev else None, extra_meta={"scene_id": scene.id})
    scene.last_job_id = job.id
    db.commit()
    return job


def narration_text(scene: Scene) -> str:
    """What is spoken: the scene's script/dialogue, else its description."""
    return " ".join((scene.script or scene.description or "").split())


def generate_narration(db: Session, user: User, project: Project, scene: Scene, gender: str = "", emotion: str = "") -> GenerationJob:
    """Queues ONE normal voice job for the scene's text (same voice allowance, refund and feature rules as any voice)."""
    prev = db.get(GenerationJob, scene.narration_job_id) if scene.narration_job_id else None
    if prev and prev.status in ACTIVE_STATUSES:
        raise AppError("This scene's narration is already being generated.", 409, "scene_busy")
    text = narration_text(scene)
    if not text:
        raise AppError("Add a script or description to this scene first: that is the text that will be spoken.", 422, "validation_error")
    options = {k: v for k, v in (("gender", gender), ("emotion", emotion)) if v}
    job = generation.submit(db, user, "voice", text, text, options, project.id, [], parent_id=prev.id if prev else None,
                            extra_meta={"scene_id": scene.id, "scene_narration": True})
    scene.narration_job_id = job.id
    db.commit()
    return job


def attach_narration(db: Session, scene_id: str, asset: GeneratedAsset) -> None:
    scene = db.get(Scene, scene_id)
    if scene and scene.project_id == asset.project_id:
        scene.narration_asset_id = asset.id
        db.commit()


def narration_asset(db: Session, scene: Scene) -> GeneratedAsset | None:
    a = db.get(GeneratedAsset, scene.narration_asset_id) if scene.narration_asset_id else None
    return a if a and a.file_path else None


def attach_video(db: Session, scene_id: str, asset: GeneratedAsset) -> None:
    """Called when a scene's video job stores its result: the newest clip becomes the scene's clip."""
    scene = db.get(Scene, scene_id)
    if scene and scene.project_id == asset.project_id:
        scene.video_asset_id = asset.id
        db.commit()

