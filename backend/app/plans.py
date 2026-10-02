"""Central plan configuration. Everything about what a plan is and allows lives here; routers and services never hard-code
plan names, prices or limits. Prices come from settings (env), allowances from the table below (or Admin -> Plans).

Movie-themed tiers:  teaser (free) -> trailer -> movie.   Allowances are per calendar month (UTC).
"""
from dataclasses import dataclass, field

from .config import get_settings

DEFAULT_PLAN_ID = "teaser"
HARD_MAX_VIDEO_SECONDS = 30          # no plan may exceed this for ONE generated clip; also enforced in generation_schema

# Monthly allowance of the free TEASER plan, per generator id. Videos are the headline limit: teaser 5, trailer 15, movie 40.
# Every other generator scales with the same ratio (1x / 3x / 8x).
VIDEO_LIMITS = {"teaser": 5, "trailer": 15, "movie": 40}
TEASER_LIMITS: dict[str, int] = {
    "story": 20, "script": 10, "lyrics": 20, "music": 10, "voice": 20, "video": VIDEO_LIMITS["teaser"], "image": 30,
    "face_replacement": 3, "ai_avatar": 3, "interactive_avatar": 12,
}
MULTIPLIERS = {"teaser": 1, "trailer": 3, "movie": 8}

# Plan ids used by earlier releases. Rows are migrated by Alembic 0008; this is only a safety net for anything left behind.
LEGACY_PLAN_IDS = {"audience": "teaser", "indie_director": "trailer", "studio": "movie", "indie": "trailer", "blockbuster": "movie"}


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
    usage_period: str = "month"          # "day" | "month": the window the limits apply to
    active: bool = True
    sort: int = 0

    def __post_init__(self):
        # Safety net: a misconfigured plan can never allow more than the global clip cap.
        object.__setattr__(self, "features", {**self.features, "max_video_seconds": min(
            int(self.features.get("max_video_seconds", HARD_MAX_VIDEO_SECONDS)), HARD_MAX_VIDEO_SECONDS)})

    @property
    def is_paid(self) -> bool:
        return self.price_minor > 0


def _limits(plan_id: str) -> dict[str, int]:
    limits = {g: n * MULTIPLIERS[plan_id] for g, n in TEASER_LIMITS.items()}
    limits["video"] = VIDEO_LIMITS[plan_id]
    return limits


_FEATURES = {"max_video_seconds": 30, "image_to_video": True, "face_replacement": True}


def _build() -> dict[str, Plan]:
    s = get_settings()
    plans = [
        Plan("teaser", "Teaser", "A first look", "Try DreamCast for free: 5 videos a month.", 0, "INR", "free", _limits("teaser"), _FEATURES, sort=0),
        Plan("trailer", "Trailer", "Start making your own films", "15 videos a month and a bigger allowance for everything else.",
             max(0, s.plan_trailer_price_inr) * 100, "INR", "month", _limits("trailer"), _FEATURES, sort=1),
        Plan("movie", "Movie", "For serious production", "40 videos a month and the largest allowance for everything else.",
             max(0, s.plan_movie_price_inr) * 100, "INR", "month", _limits("movie"), _FEATURES, sort=2),
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
