"""Central plan configuration. Everything about what a plan is and allows lives here; routers and services never hard-code
plan names, prices or limits. Prices come from settings (env), allowances from the table below (or Admin -> Limits).

Movie-themed tiers:  trailer (free) -> indie -> blockbuster
"""
from dataclasses import dataclass, field

from .config import get_settings

DEFAULT_PLAN_ID = "trailer"
HARD_MAX_VIDEO_SECONDS = 30          # no plan may exceed this for ONE generated clip; also enforced in generation_schema

# TRAILER (free) allowance per usage period (a day), per generator id. Paid plans are multiples of it (see MULTIPLIERS).
TRAILER_LIMITS: dict[str, int] = {
    "story": 5, "script": 3, "lyrics": 5, "music": 3, "voice": 5, "video": 3,
    "face_replacement": 3, "ai_avatar": 3, "interactive_avatar": 12,
}
MULTIPLIERS = {"trailer": 1, "indie": 4, "blockbuster": 16}   # each tier 4x the previous one

# Plan ids used before the Phase 5 rename. Old rows map to the closest current plan so nobody loses or gains access by accident.
LEGACY_PLAN_IDS = {"audience": "trailer", "indie_director": "indie", "studio": "blockbuster"}


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    tagline: str
    description: str
    price_minor: int                     # smallest currency unit (paise); 0 = free
    currency: str
    billing_period: str                  # "free" | "month"
    limits: dict[str, int]               # generations allowed per usage_period
    features: dict[str, object] = field(default_factory=dict)   # entitlements enforced by the backend
    usage_period: str = "day"            # "day" | "month": the window the limits apply to
    active: bool = True
    sort: int = 0

    def __post_init__(self):
        # Safety net: a misconfigured plan can never allow more than the global clip cap.
        object.__setattr__(self, "features", {**self.features, "max_video_seconds": min(
            int(self.features.get("max_video_seconds", HARD_MAX_VIDEO_SECONDS)), HARD_MAX_VIDEO_SECONDS)})

    @property
    def is_paid(self) -> bool:
        return self.price_minor > 0


def _limits(factor: int) -> dict[str, int]:
    return {g: n * factor for g, n in TRAILER_LIMITS.items()}


_FEATURES = {"max_video_seconds": 30, "image_to_video": True, "face_replacement": True}


def _build() -> dict[str, Plan]:
    s = get_settings()
    plans = [
        Plan("trailer", "Trailer", "Watch the story", "Try DreamCast with a small free allowance.", 0, "INR", "free",
             _limits(MULTIPLIERS["trailer"]), _FEATURES, sort=0),
        Plan("indie", "Indie", "Start making your own films", "More of everything for solo creators.",
             max(0, s.plan_indie_price_inr) * 100, "INR", "month", _limits(MULTIPLIERS["indie"]), _FEATURES, sort=1),
        Plan("blockbuster", "Blockbuster", "For serious production", "The largest allowance for heavy production.",
             max(0, s.plan_blockbuster_price_inr) * 100, "INR", "month", _limits(MULTIPLIERS["blockbuster"]), _FEATURES, sort=2),
    ]
    return {p.id: p for p in plans}


PLANS: dict[str, Plan] = _build()


def get_plan(plan_id: str | None) -> Plan:
    """Unknown or retired plan ids fall back to the free plan, so a bad row can never lock a user out or grant extra access."""
    plan_id = LEGACY_PLAN_IDS.get(plan_id or "", plan_id or "")
    plan = PLANS.get(plan_id)
    return plan if plan and plan.active else PLANS[DEFAULT_PLAN_ID]


def public_plans() -> list[Plan]:
    return sorted((p for p in PLANS.values() if p.active), key=lambda p: p.sort)
