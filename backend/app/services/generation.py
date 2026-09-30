"""Validation + submission for generation requests (shared by refine, create and regenerate)."""
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError, NotFound
from ..generators import BY_ID, canonical_generator
from ..models import GeneratedAsset, GenerationJob, Project, ReferenceAsset, User
from ..providers import ProviderError
from . import context as ctx_service
from . import jobs, provider_settings, usage
from .generation_schema import AUX_KEYS, SPECS, normalize_options, validate_prompt

MAX_REFINED = 8000
MAX_REFERENCES = 3


@dataclass
class Prepared:
    generator: str
    project: Project | None
    prompt: str
    options: dict
    references: list[ReferenceAsset]
    warnings: list[str] = field(default_factory=list)


def owned_project_or_404(db: Session, user: User, project_id: str | None) -> Project | None:
    if not project_id:
        return None
    p = db.get(Project, project_id)
    if not p or p.user_id != user.id:
        raise NotFound("Project not found.")
    return p


def prepare(db: Session, user: User, generator_type: str, prompt: str, options: dict | None, project_id: str | None,
            reference_ids: list[str] | None, *, strict: bool) -> Prepared:
    generator = canonical_generator(generator_type)
    if not generator:
        raise AppError("Unsupported generator type.", 400, "unsupported_generator")
    spec = SPECS[generator]
    project = owned_project_or_404(db, user, project_id)
    clean, warnings = normalize_options(generator, options, prompt or "", strict=strict)
    prompt = validate_prompt(generator, prompt, clean)
    refs: list[ReferenceAsset] = []
    ids = list(dict.fromkeys(reference_ids or []))
    if generator == "video" and clean.get("method") == "Image to Video" and not ids:
        raise AppError("Choose or upload an image for image-to-video.", 422, "validation_error")
    if ids and not spec.reference:
        raise AppError("This generator doesn't use reference images.", 422, "validation_error")
    if len(ids) > MAX_REFERENCES:
        raise AppError(f"You can attach up to {MAX_REFERENCES} reference images.", 422, "validation_error")
    if ids:
        if not project:
            raise AppError("Select a project to use reference images.", 422, "validation_error")
        refs = list(db.scalars(select(ReferenceAsset).where(ReferenceAsset.id.in_(ids), ReferenceAsset.project_id == project.id)))
        if len(refs) != len(ids):
            raise AppError("One of the reference images was not found in this project.", 422, "validation_error")
    if generator == "face_replacement":
        _validate_face_inputs(clean, refs, strict)
    _validate_asset_refs(db, project, clean)
    if generator in ("video", "face_replacement") and not strict:
        warnings += _variations_note(db, user, generator, prompt)
    warnings += _check_provider_capabilities(db, generator, clean, prompt, strict,
                                             [{"id": r.id, "type": r.type, "mime_type": r.mime_type} for r in refs])
    return Prepared(generator, project, prompt, clean, refs, warnings)


def _validate_face_inputs(options: dict, refs: list[ReferenceAsset], strict: bool) -> None:
    by_id = {r.id: r for r in refs}
    src, face = by_id.get(options.get("source_asset_id")), by_id.get(options.get("face_asset_id"))
    if strict or options.get("source_asset_id") or options.get("face_asset_id"):
        if not src:
            raise AppError("Choose or upload the source image or video (requires a project).", 422, "validation_error")
        if not face or not face.mime_type.startswith("image/"):
            raise AppError("Choose or upload an image of the face to use.", 422, "validation_error")
        if src.id == face.id:
            raise AppError("The source and the face must be different files.", 422, "validation_error")
    if strict and options.get("permission_confirmed") is not True:
        raise AppError("Please confirm that you have permission to use the face/image you uploaded.", 422, "permission_required")


def _variations_note(db: Session, user: User, generator: str, prompt: str) -> list[str]:
    """DreamCast never silently makes several generations: if the prompt asks for N versions, say what's actually possible."""
    m = re.search(r"\b(\d+|two|three|four|five)\s+(versions?|variations?|options|takes|videos)\b", prompt or "", re.I)
    n = {"two": 2, "three": 3, "four": 4, "five": 5}.get((m.group(1) if m else "").lower()) or (int(m.group(1)) if m and m.group(1).isdigit() else 1)
    if n <= 1:
        return []
    label = BY_ID[generator].label.lower()
    left = max(0, usage.get_limits(db)[generator] - usage.used_today(db, user.id).get(generator, 0))
    return [f"DreamCast creates one {label} per generation, so this makes one. You have {left} {label} generation{'s' if left != 1 else ''} "
            f"remaining today; generate again for another version."]


def _validate_asset_refs(db: Session, project: Project | None, options: dict) -> None:
    """Selected story/lyrics/script assets must exist in the same project (which the user owns)."""
    picked = {k: options[k] for k in AUX_KEYS if options.get(k)}
    if options.get("scene_number") and not options.get("script_asset_id"):
        raise AppError("Choose a script to use one of its scenes.", 422, "validation_error")
    if not picked:
        return
    if not project:
        raise AppError("Select a project to use existing stories, scripts or lyrics.", 422, "validation_error")
    for key, asset_id in picked.items():
        a = db.get(GeneratedAsset, asset_id)
        if not a or a.project_id != project.id or a.type != AUX_KEYS[key] or not a.text_content:
            raise AppError("The selected story, script or lyrics could not be found in this project.", 422, "validation_error")


def _check_provider_capabilities(db: Session, generator: str, options: dict, prompt: str, strict: bool, refs: list[dict]) -> list[str]:
    """Checks the request against what the active provider can really do (e.g. music duration, voice language) so an
    unsupported request is explained up front instead of being sent to the provider. Returns informational notes."""
    try:
        provider = provider_settings.select_provider(db, generator, allow_unconfigured=True)
    except ProviderError:
        return []           # disabled/capped/missing providers are reported by check_can_submit
    notes: list[str] = provider.adapt_options(generator, options) if not strict else []
    try:
        notes += provider.validate_options(generator, options, prompt if generator in ("voice", "face_replacement") else "", refs)
    except ProviderError as e:
        raise AppError(e.user_message, 422, e.code.value)
    return notes


def build_context(db: Session, prep: Prepared, full: bool = False) -> dict:
    return ctx_service.get_context(db, prep.project, prep.generator, character_ids=prep.options.get("character_ids"),
                                   reference_ids=[r.id for r in prep.references] or None, options=prep.options, full=full)


def check_can_submit(db: Session, user: User, generator: str):
    """Order matters: usage limit first (cheap, user-facing), then provider availability."""
    if not usage.has_quota(db, user.id, generator):
        raise AppError(f"You've reached today's {BY_ID[generator].label} limit. It resets at midnight UTC.", 429, "QUOTA_EXCEEDED")
    try:
        return provider_settings.select_provider(db, generator, allow_unconfigured=True)
    except ProviderError as e:
        raise AppError(e.user_message, 503, e.code.value)


def submit(db: Session, user: User, generator_type: str, original_prompt: str, refined_prompt: str, options: dict | None,
           project_id: str | None, reference_ids: list[str] | None, parent_id: str | None = None,
           lineage_id: str | None = None) -> GenerationJob:
    prep = prepare(db, user, generator_type, original_prompt, options, project_id, reference_ids, strict=True)
    refined = (refined_prompt or "").strip()
    if not refined:
        raise AppError("The refined prompt is empty. Refine your prompt first, or write one.", 422, "validation_error")
    if len(refined) > MAX_REFINED:
        raise AppError(f"The refined prompt is too long (maximum {MAX_REFINED} characters).", 422, "validation_error")
    if parent_id:
        parent = db.get(GenerationJob, parent_id)
        if not parent or parent.user_id != user.id:
            raise NotFound("Generation not found.")
    provider = check_can_submit(db, user, prep.generator)
    return jobs.create_job(db, user.id, prep.generator, project_id=prep.project.id if prep.project else None,
                           original_prompt=prep.prompt, refined_prompt=refined, options=prep.options,
                           reference_assets=[r.id for r in prep.references], context=build_context(db, prep, full=True),
                           provider=provider.name, parent_id=parent_id,
                           input_meta={"lineage_id": lineage_id} if lineage_id else None)
