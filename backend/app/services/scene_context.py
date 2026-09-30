"""Turns a stored script scene or story section into ready-to-use video context (shared by the Create page and the API)."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound
from ..models import Character, GeneratedAsset
from ..textparse import compose_video_prompt, parse_scene_fields, parse_scenes, parse_story_sections, scene_text


def matching_characters(db: Session, project_id: str, text: str) -> list[Character]:
    """Project characters whose name (or a name part of 3+ letters) appears as a word in the scene text."""
    hay = text.lower()

    def mentioned(name: str) -> bool:
        return any(re.search(rf"\b{re.escape(w)}\b", hay) for w in [name.lower(), *[p for p in name.lower().split() if len(p) >= 3]])

    return [c for c in db.scalars(select(Character).where(Character.project_id == project_id).order_by(Character.created_at)) if mentioned(c.name)]


def scene_fields(asset: GeneratedAsset, number: int) -> dict:
    scene = next((s for s in parse_scenes(asset.text_content or "") if s["number"] == number), None)
    if not scene:
        raise NotFound("Scene not found in this script.")
    return {"number": number, **parse_scene_fields(scene_text(asset.text_content, scene))}


def scene_detail(db: Session, asset: GeneratedAsset, number: int) -> dict:
    f = scene_fields(asset, number)
    chars = matching_characters(db, asset.project_id, f"{f['characters']} {f['action']}")
    return {**f, "video_prompt": compose_video_prompt(f), "character_ids": [c.id for c in chars],
            "characters": [c.name for c in chars]}


def story_sections(asset: GeneratedAsset) -> list[dict]:
    return parse_story_sections(asset.text_content or "")
