"""ProjectContextService: gathers only the project information relevant to a generator (never the whole database)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Character, GeneratedAsset, Project, ReferenceAsset
from ..textparse import parse_scene_fields, parse_scenes, parse_story_sections, scene_text

MAX_CHARACTERS = 5
TEXT_LIMIT = 500

# What each generator gets: which pieces of project context are relevant.
RULES = {
    # Video: nothing is included unless the user selected it (characters/references/story section/scene), to keep prompts relevant and cheap.
    "video": {"story": False, "script": False, "characters": True, "references": True},
    "music": {"story": True, "script": False, "characters": False, "references": False},
    "voice": {"story": False, "script": True, "characters": True, "references": False},
    "lyrics": {"story": True, "script": False, "characters": False, "references": False},
    "story": {"story": True, "script": False, "characters": True, "references": False},
    "script": {"story": True, "script": False, "characters": True, "references": False},
    "face_replacement": {"story": False, "script": False, "characters": False, "references": True},
    "ai_avatar": {"story": False, "script": False, "characters": True, "references": True},
    "interactive_avatar": {"story": True, "script": False, "characters": True, "references": True},
}
CHARACTER_FIELDS = {"video": ("appearance", "clothing"), "voice": ("personality",), "story": ("description", "personality"),
                    "script": ("description", "personality"), "ai_avatar": ("appearance",),
                    "interactive_avatar": ("appearance", "personality")}


def _clip(text: str | None, n: int = TEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _latest_text(db: Session, project_id: str, type_: str) -> str:
    a = db.scalars(select(GeneratedAsset).where(GeneratedAsset.project_id == project_id, GeneratedAsset.type == type_,
                                                GeneratedAsset.text_content.is_not(None), GeneratedAsset.status == "READY")
                   .order_by(GeneratedAsset.created_at.desc()).limit(1)).first()
    return _clip(a.text_content) if a else ""


def _asset(db: Session, project_id: str, asset_id: str | None, type_: str) -> GeneratedAsset | None:
    if not asset_id:
        return None
    a = db.get(GeneratedAsset, asset_id)
    return a if a and a.project_id == project_id and a.type == type_ and a.text_content else None


def get_context(db: Session, project: Project | None, generator: str, *, character_ids: list[str] | None = None,
                reference_ids: list[str] | None = None, options: dict | None = None, full: bool = False) -> dict:
    """full=False -> concise context for the prompt-refinement call. full=True -> the fuller (still bounded) context stored
    on the job for the generation call, e.g. the complete selected story when turning it into a script."""
    if project is None:
        return {}
    options = options or {}
    rules = RULES[generator]
    ctx: dict = {"project": {"title": project.title, "genre": project.genre, "description": _clip(project.description, 300),
                             "style": _clip(str((project.meta or {}).get("style") or ""), 80)}}
    if rules["characters"]:
        q = select(Character).where(Character.project_id == project.id).order_by(Character.created_at)
        chars = db.scalars(q).all()
        if generator == "video" and character_ids is None:
            chars = []                                   # only characters the user picked
        elif character_ids is not None:
            chars = [c for c in chars if c.id in set(character_ids)]
        fields = CHARACTER_FIELDS.get(generator, ("description",))
        ctx["characters"] = [{"name": c.name, "age": c.age, **{f: _clip(getattr(c, f), 160) for f in fields if getattr(c, f)}}
                             for c in chars[:MAX_CHARACTERS]]
    if rules["story"] and (t := _latest_text(db, project.id, "STORY")):
        ctx["story"] = t
    if rules["script"] and (t := _latest_text(db, project.id, "SCRIPT")):
        ctx["script"] = t
    if rules["references"] and not (generator == "video" and not reference_ids):
        q = select(ReferenceAsset).where(ReferenceAsset.project_id == project.id)
        if reference_ids:
            q = q.where(ReferenceAsset.id.in_(reference_ids))
        refs = db.scalars(q.order_by(ReferenceAsset.created_at.desc()).limit(6)).all()
        ctx["references"] = [{"name": r.name, "type": r.type} for r in refs]
    # Explicitly selected assets (validated to belong to this project by the caller).
    if story := _asset(db, project.id, options.get("story_asset_id"), "STORY"):
        ctx["story"] = _clip(story.text_content)
        ctx["story_title"] = story.title
        if full:
            ctx["story_full"] = _clip(story.text_content, 4000)
    if lyr := _asset(db, project.id, options.get("lyrics_asset_id"), "LYRICS"):
        ctx["lyrics"] = _clip(lyr.text_content, 1200 if full else 400)
    scr = _asset(db, project.id, options.get("script_asset_id"), "SCRIPT")
    if scr and options.get("scene_number"):
        scene = next((s for s in parse_scenes(scr.text_content) if s["number"] == options["scene_number"]), None)
        if scene:
            ctx["scene"] = f"Scene {scene['number']}: " + _clip(scene_text(scr.text_content, scene), 600 if full else 350)
    if generator == "video":
        _video_sources(db, project, options, ctx, full)
    return {k: v for k, v in ctx.items() if v}


def _video_sources(db: Session, project: Project, options: dict, ctx: dict, full: bool) -> None:
    """Video-only context: the selected script scene as structured fields, and the selected story section."""
    scr = _asset(db, project.id, options.get("script_asset_id"), "SCRIPT")
    if scr and options.get("scene_number"):
        scene = next((x for x in parse_scenes(scr.text_content) if x["number"] == options["scene_number"]), None)
        if scene:
            f = parse_scene_fields(scene_text(scr.text_content, scene))
            keep = {k: _clip(f[k], 300 if full else 180) for k in ("heading", "environment", "action", "camera", "lighting", "sound", "music_cue") if f.get(k)}
            keep["number"] = scene["number"]
            if f["dialogue"]:
                keep["dialogue"] = [f"{d['speaker']}: {_clip(d['line'], 100)}" for d in f["dialogue"][:2]]
            ctx["scene_fields"] = keep
            ctx.pop("scene", None)
    story = _asset(db, project.id, options.get("story_asset_id"), "STORY")
    if story and options.get("story_section"):
        section = next((x for x in parse_story_sections(story.text_content) if x["key"] == options["story_section"]), None)
        if section:
            ctx["story_section"] = f"{section['key']}: {_clip(section['text'], 600 if full else 400)}"
            ctx.pop("story", None)
            ctx.pop("story_full", None)


def summarize(ctx: dict) -> dict:
    """Small, safe description of what context was used (shown to the user)."""
    return {"project": bool(ctx.get("project")), "characters": len(ctx.get("characters", [])),
            "references": len(ctx.get("references", [])), "story": "story" in ctx, "script": "script" in ctx,
            "lyrics": "lyrics" in ctx, "scene": "scene" in ctx or "scene_fields" in ctx, "story_section": "story_section" in ctx}


def context_text(ctx: dict) -> str:
    lines = []
    if p := ctx.get("project"):
        lines.append(f"Project: {p['title']}" + (f" ({p['genre']})" if p.get("genre") else "") +
                     (f". {p['description']}" if p.get("description") else ""))
        if p.get("style"):
            lines.append(f"Project visual style: {p['style']} (use it only if the request doesn't choose a style)")
    for c in ctx.get("characters", []):
        details = "; ".join(f"{k}: {v}" for k, v in c.items() if k not in ("name", "age"))
        lines.append(f"Character: {c['name']}" + (f", {c['age']}" if c.get("age") else "") + (f" — {details}" if details else ""))
    if s := ctx.get("story"):
        lines.append(f"Story so far: {s}")
    if s := ctx.get("script"):
        lines.append(f"Script excerpt: {s}")
    if s := ctx.get("lyrics"):
        lines.append(f"Lyrics (for theme and mood only): {s}")
    if s := ctx.get("scene"):
        lines.append(f"Scene: {s}")
    if f := ctx.get("scene_fields"):
        lines.append(f"Script scene {f['number']}: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in f.items() if k not in ("number", "dialogue")))
        if f.get("dialogue"):
            lines.append("Dialogue (context only): " + " / ".join(f["dialogue"]))
    if s := ctx.get("story_section"):
        lines.append(f"Story section: {s}")
    if refs := ctx.get("references"):
        lines.append("Reference images: " + ", ".join(f"{r['name']} ({r['type'].lower()})" for r in refs))
    return "\n".join(lines)
