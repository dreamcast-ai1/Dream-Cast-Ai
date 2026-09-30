"""Admin-editable provider settings (enable/disable, optional daily cap) and availability checks."""
from sqlalchemy.orm import Session

from ..models import AppSetting
from ..providers import ErrorCode, Provider, ProviderError, registry
from . import usage

KEY = "provider_settings"


def _all(db: Session) -> dict:
    row = db.get(AppSetting, KEY)
    return dict(row.value) if row else {}


def get(db: Session, name: str) -> dict:
    return {"enabled": True, "daily_cap": None, **_all(db).get(name, {})}


def update(db: Session, name: str, *, enabled: bool | None = None, daily_cap: int | None | str = "keep") -> dict:
    data = _all(db)
    cur = {"enabled": True, "daily_cap": None, **data.get(name, {})}
    if enabled is not None:
        cur["enabled"] = enabled
    if daily_cap != "keep":
        cur["daily_cap"] = daily_cap
    data[name] = cur
    row = db.get(AppSetting, KEY)
    if row:
        row.value = data
    else:
        db.add(AppSetting(key=KEY, value=data))
    db.commit()
    return cur


def describe(db: Session) -> list[dict]:
    out = []
    for p in registry.all():
        st = get(db, p.name)
        out.append({"name": p.name, "label": p.label or p.capability.value.title(), "capability": p.capability.value,
                    "generators": sorted(p.generators), "configured": p.is_configured(), "problems": p.validate_config(),
                    "simulated": p.simulated, "supports_cancel": p.supports_cancel, "enabled": st["enabled"],
                    "daily_cap": st["daily_cap"], "used_today": usage.provider_used_today(db, p.name), "info": p.info(),
                    # The provider's own remaining quota is unknown unless a provider exposes it; never invented.
                    "provider_quota": "unknown"})
    return out


def select_provider(db: Session, generator: str, *, allow_unconfigured: bool = False) -> Provider:
    """First enabled provider with room under its admin-set cap that is configured.
    allow_unconfigured=True (used when a request is submitted) returns an enabled-but-unconfigured provider so the job can be
    created and fail with a clear configuration message; the runner always uses the strict form."""
    candidates = registry.for_generator(generator)
    if not candidates:
        raise ProviderError(ErrorCode.API_NOT_CONFIGURED, f"no provider registered for {generator}")
    reason: ProviderError | None = None
    unconfigured: Provider | None = None
    for p in candidates:
        st = get(db, p.name)
        if not st["enabled"]:
            reason = reason or ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"{p.name} disabled by admin",
                                             message=f"The {p.label.lower() or 'generation'} provider has been disabled by an administrator.")
        elif not p.is_configured():
            unconfigured = unconfigured or p
        elif st["daily_cap"] is not None and usage.provider_used_today(db, p.name) >= st["daily_cap"]:
            reason = ProviderError(ErrorCode.QUOTA_EXCEEDED, f"{p.name} daily cap reached")
        else:
            return p
    if unconfigured and (allow_unconfigured or not reason):
        if allow_unconfigured:
            return unconfigured
        raise ProviderError(ErrorCode.API_NOT_CONFIGURED, f"{unconfigured.name}: {'; '.join(unconfigured.validate_config())}",
                            message=unconfigured.not_configured_message)
    raise reason  # type: ignore[misc]
