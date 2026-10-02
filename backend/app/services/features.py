"""Feature switches: the single place that decides what users may use. Stored in the database (AppSetting 'feature_flags'), so an
administrator can turn features on and off from the Admin Dashboard without a deploy. Defaults apply until an admin changes a switch.

The same answer drives the UI (/api/features) and the API itself (require_generator / check_languages are called by every route that
starts a generation), so a hidden feature cannot be used by calling its endpoint directly. Existing assets are never touched."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..errors import AppError
from ..generators import ASSET_TYPES, GENERATORS
from ..models import AppSetting
from ..providers import prompt_refiner, registry
from . import provider_settings

KEY = "feature_flags"


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    description: str
    default: bool
    kind: str                      # generator | language | refinement
    generator: str | None = None   # the generator id a switch controls (face_swap -> face_replacement)


FEATURES: list[Feature] = [
    Feature("video", "Video", "Text and image to video clips.", True, "generator", "video"),
    Feature("image", "Image", "Pictures from a description.", True, "generator", "image"),
    Feature("music", "Music", "Instrumental music, and vocals where the provider supports them.", True, "generator", "music"),
    Feature("voice", "Voice", "Text to speech with gender and emotion.", True, "generator", "voice"),
    Feature("lyrics", "Lyrics", "Song lyrics from an idea.", True, "generator", "lyrics"),
    Feature("story", "Story", "Structured stories you can edit.", True, "generator", "story"),
    Feature("script", "Script", "Scene-by-scene scripts from a story.", True, "generator", "script"),
    Feature("face_swap", "Face Swap", "Replace a face in an image or video. Needs a face provider.", False, "generator", "face_replacement"),
    Feature("ai_avatar", "AI Avatar", "Talking avatars. Needs an avatar provider.", False, "generator", "ai_avatar"),
    Feature("interactive_avatar", "Interactive Avatar", "Avatars you can talk to. Needs an avatar provider.", False, "generator", "interactive_avatar"),
    Feature("hindi", "Hindi", "Offer Hindi as a language for stories, lyrics and voice.", False, "language"),
    Feature("telugu", "Telugu", "Offer Telugu as a language for stories, lyrics and voice.", False, "language"),
    Feature("prompt_refinement", "AI prompt refinement", "Improve prompts with the configured AI (LLM_PROVIDER).", True, "refinement"),
    Feature("free_refinement", "Basic (free) refinement", "A built-in, non-AI way to structure prompts. Works without any API key.", True, "refinement"),
    Feature("prefer_ai_refinement", "Prefer AI when available", "Use AI refinement first. Off = always use basic refinement.", True, "refinement"),
    Feature("auto_fallback_refinement", "Fall back to basic refinement automatically",
            "When the AI is unavailable, disabled or its allowance is used up, use basic refinement instead of nothing.", True, "refinement"),
]
BY_ID = {f.id: f for f in FEATURES}
GENERATOR_FEATURE = {f.generator: f.id for f in FEATURES if f.generator}
LANGUAGE_FEATURES = {"Hindi": "hindi", "Telugu": "telugu"}      # English is always available


def _stored(db: Session) -> dict:
    row = db.get(AppSetting, KEY)
    return dict(row.value) if row else {}


def all_flags(db: Session) -> dict[str, bool]:
    stored = _stored(db)
    return {f.id: bool(stored.get(f.id, f.default)) for f in FEATURES}


def is_enabled(db: Session, feature_id: str) -> bool:
    return all_flags(db)[feature_id]


def set_flags(db: Session, changes: dict[str, bool]) -> dict[str, bool]:
    unknown = [k for k in changes if k not in BY_ID]
    if unknown:
        raise AppError(f"Unknown feature: {unknown[0]}.", 422, "validation_error")
    data = _stored(db)
    data.update({k: bool(v) for k, v in changes.items()})
    row = db.get(AppSetting, KEY)
    if row:
        row.value = data
    else:
        db.add(AppSetting(key=KEY, value=data))
    db.commit()
    return all_flags(db)


def generator_enabled(db: Session, generator_id: str) -> bool:
    return all_flags(db)[GENERATOR_FEATURE[generator_id]]


def require_generator(db: Session, generator_id: str) -> None:
    """Server-side gate: called by every route that starts a generation."""
    if not generator_enabled(db, generator_id):
        raise AppError("This feature isn't available right now.", 403, "feature_disabled")


def enabled_languages(db: Session) -> list[str]:
    flags = all_flags(db)
    return ["English", *[lang for lang, fid in LANGUAGE_FEATURES.items() if flags[fid]]]


def check_language(db: Session, value: str | None) -> None:
    if value in LANGUAGE_FEATURES and not is_enabled(db, LANGUAGE_FEATURES[value]):
        raise AppError(f"{value} isn't available right now. Please choose English.", 422, "language_unavailable")


def check_languages(db: Session, options: dict) -> None:
    """Language-like options (a language, or a voice accent that is a language) must be enabled."""
    for key in ("language", "accent"):
        check_language(db, options.get(key))


def filter_languages(db: Session, choices: list[str]) -> list[str]:
    allowed = set(enabled_languages(db))
    return [c for c in choices if c not in LANGUAGE_FEATURES or c in allowed]


def availability(db: Session) -> dict:
    flags = all_flags(db)
    generators = {g.id: flags[GENERATOR_FEATURE[g.id]] for g in GENERATORS}
    # An asset type is available while at least one generator that produces it is (AVATAR is shared by two generators).
    asset_types = {t: any(generators[g.id] for g in GENERATORS if g.asset_type == t) or not any(g.asset_type == t for g in GENERATORS) for t in ASSET_TYPES}
    return {"generators": generators, "asset_types": asset_types, "hidden": sorted(g for g, on in generators.items() if not on),
            "languages": enabled_languages(db),
            "refinement": {"ai": flags["prompt_refinement"], "basic": flags["free_refinement"]}}


def provider_status(db: Session, feature: Feature) -> dict | None:
    """Configuration status only, never a secret. 'configured' | 'not_configured' | 'disabled' | None (nothing to configure)."""
    if feature.kind == "language" or feature.id in ("free_refinement", "prefer_ai_refinement", "auto_fallback_refinement"):
        return None
    if feature.id == "prompt_refinement":
        llm = prompt_refiner()
        return {"status": "configured" if llm.is_configured() else "not_configured", "provider": llm.name if llm.is_configured() else None}
    providers = registry.for_generator(feature.generator)
    if not providers:
        return {"status": "not_configured", "provider": None, "message": "No provider is built in for this feature yet."}
    usable = [p for p in providers if p.is_configured() and provider_settings.get(db, p.name)["enabled"]]
    if usable:
        return {"status": "configured", "provider": usable[0].name}
    if any(p.is_configured() for p in providers):
        return {"status": "disabled", "provider": providers[0].name, "message": "The provider is switched off in Providers."}
    return {"status": "not_configured", "provider": providers[0].name, "message": providers[0].not_configured_message}


def describe(db: Session) -> list[dict]:
    flags = all_flags(db)
    return [{"id": f.id, "label": f.label, "description": f.description, "kind": f.kind, "enabled": flags[f.id], "default": f.default,
             "provider": provider_status(db, f)} for f in FEATURES]
