"""The single place that decides which features users should see.

Core generators (story, script, lyrics, music, voice, video, image) are always offered. The optional ones (face replacement, AI avatar,
interactive avatar) are offered only while some registered provider can really run them: it is configured (credentials present), and an
administrator has not switched it off. Nothing here deletes or changes a feature: set the credentials and it shows up, with no code change.
The frontend asks /api/features once and never decides on its own."""
from sqlalchemy.orm import Session

from ..generators import ASSET_TYPES, GENERATORS, OPTIONAL_GENERATORS
from ..providers import registry
from . import provider_settings


def generator_enabled(db: Session, generator_id: str) -> bool:
    if generator_id not in OPTIONAL_GENERATORS:
        return True
    return any(p.is_configured() and provider_settings.get(db, p.name)["enabled"] for p in registry.for_generator(generator_id))


def availability(db: Session) -> dict:
    generators = {g.id: generator_enabled(db, g.id) for g in GENERATORS}
    # An asset type is available while at least one generator that produces it is (AVATAR is shared by two generators).
    asset_types = {t: any(generators[g.id] for g in GENERATORS if g.asset_type == t) or not any(g.asset_type == t for g in GENERATORS) for t in ASSET_TYPES}
    return {"generators": generators, "asset_types": asset_types, "hidden": sorted(g for g, on in generators.items() if not on)}
