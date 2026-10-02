"""Catalogue of generators. Single source of truth shared with the frontend via /api/generators."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Generator:
    id: str
    label: str
    emoji: str
    description: str
    asset_type: str        # GeneratedAsset.type produced by this generator
    project_section: str   # workspace tab that will hold its output


GENERATORS: list[Generator] = [
    Generator("video", "Video", "🎬", "Turn ideas into video clips and scenes.", "VIDEO", "videos"),
    Generator("image", "Image", "🖼️", "Create pictures from a description.", "IMAGE", "images"),
    Generator("music", "Music", "🎵", "Compose original music and soundtracks.", "MUSIC", "music"),
    Generator("voice", "Voice", "🎤", "Generate narration and character voices.", "VOICE", "voice"),
    Generator("lyrics", "Lyrics", "✍️", "Write song lyrics in any style.", "LYRICS", "lyrics"),
    Generator("story", "Story", "📖", "Develop stories, plots and worlds.", "STORY", "story"),
    Generator("script", "Script", "📝", "Draft screenplays and dialogue.", "SCRIPT", "script"),
    Generator("face_replacement", "Face Replacement", "👤", "Swap faces in images and video.", "FACE", "references"),
    Generator("ai_avatar", "AI Avatar", "🧑", "Create talking AI avatars.", "AVATAR", "avatars"),
    Generator("interactive_avatar", "Interactive Avatar", "💬", "Avatars you can talk to in real time.", "AVATAR", "avatars"),
]

GENERATOR_IDS = [g.id for g in GENERATORS]

# Features that need their own paid AI provider. They are never removed: the UI simply hides them while no provider is configured
# (services/features.py decides, from the provider credentials), and they reappear by themselves once credentials are set.
OPTIONAL_GENERATORS = frozenset({"face_replacement", "ai_avatar", "interactive_avatar"})
BY_ID = {g.id: g for g in GENERATORS}

ASSET_TYPES = ["VIDEO", "IMAGE", "MUSIC", "VOICE", "LYRICS", "STORY", "SCRIPT", "FACE", "AVATAR"]
JOB_STATUSES = ["QUEUED", "PROCESSING", "RETRYING", "COMPLETED", "FAILED", "CANCELLED"]
ACTIVE_STATUSES = ("QUEUED", "PROCESSING", "RETRYING")

# API accepts the spec's upper-case names (FACE, AVATAR, ...) as aliases of the canonical ids.
ALIASES = {"face": "face_replacement", "avatar": "ai_avatar", "interactive_avatar": "interactive_avatar"}


def canonical_generator(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    v = ALIASES.get(v, v)
    return v if v in BY_ID else None
REFERENCE_TYPES = ["CHARACTER", "LOCATION", "OBJECT", "STYLE", "OTHER", "FACE", "SOURCE"]
ASPECT_RATIOS = ("16:9", "9:16", "1:1")
PROJECT_STATUSES = ["DRAFT", "IN_PROGRESS", "COMPLETED", "ARCHIVED"]
