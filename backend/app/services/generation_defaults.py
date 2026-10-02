"""Administrator-set defaults for generation options (duration, genre, mood, emotion, accent, voice ...), stored in the existing key/value
settings table. Normal users never see the advanced options: the server removes them from their requests and fills in these defaults instead.
Administrators see every option and can override the defaults per request."""
import re

from sqlalchemy.orm import Session

from ..errors import AppError
from ..generators import GENERATOR_IDS
from ..models import AppSetting, User
from .generation_schema import SPECS, advanced_keys

KEY = "generation_defaults"
VOICE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,60}")


def is_admin(user: User) -> bool:
    return user.role == "ADMIN"


def _stored(db: Session) -> dict:
    row = db.get(AppSetting, KEY)
    return dict(row.value) if row else {}


def configurable_fields(generator: str):
    return [f for f in SPECS[generator].fields if f.kind in ("select", "duration", "text")]


def _coerce(field, value):
    if field.kind == "duration":
        return int(value)
    return str(value)


def get_defaults(db: Session, generator: str) -> dict:
    stored = _stored(db).get(generator, {})
    keys = {f.key: f for f in configurable_fields(generator)}
    out = {}
    for k, v in stored.items():
        f = keys.get(k)
        if f is None or v in (None, ""):
            continue
        try:
            out[k] = _coerce(f, v)
        except (TypeError, ValueError):
            continue
    return out


def describe(db: Session) -> list[dict]:
    """Everything the Admin -> Defaults screen needs: each generator's configurable options, their choices and the current default."""
    out = []
    for g in GENERATOR_IDS:
        current = get_defaults(db, g)
        out.append({"generator": g, "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "choices": f.choices, "advanced": f.advanced,
                                                "default": current.get(f.key, f.default), "configured": f.key in current} for f in configurable_fields(g)]})
    return [o for o in out if o["fields"]]


def set_defaults(db: Session, generator: str, values: dict) -> dict:
    if generator not in SPECS:
        raise AppError("Unknown generator.", 404, "not_found")
    keys = {f.key: f for f in configurable_fields(generator)}
    clean: dict = {}
    for k, v in values.items():
        f = keys.get(k)
        if f is None:
            raise AppError(f"'{k}' can't be set as a default for {generator}.", 422, "validation_error")
        if v in (None, ""):
            continue                                   # empty = clear the default
        if f.kind == "text":
            if not isinstance(v, str) or not VOICE_NAME.fullmatch(v):
                raise AppError(f"Invalid value for {f.label}.", 422, "validation_error")
            clean[k] = v
        else:
            match = next((c for c in f.choices if c.lower() == str(v).lower()), None)
            if not match or match == "Custom":
                raise AppError(f"Invalid value for {f.label}.", 422, "validation_error")
            clean[k] = _coerce(f, match)
    data = _stored(db)
    data[generator] = clean
    row = db.get(AppSetting, KEY)
    if row:
        row.value = data
    else:
        db.add(AppSetting(key=KEY, value=data))
    db.commit()
    return get_defaults(db, generator)


def strip_advanced(user: User, generator: str, raw: dict | None) -> dict:
    """Normal users can't choose advanced options, even by calling the API directly: they are dropped from the request."""
    raw = dict(raw or {})
    if is_admin(user):
        return raw
    for k in advanced_keys(generator):
        raw.pop(k, None)
    return raw


def apply(db: Session, generator: str, options: dict) -> dict:
    """Fills every option the request left empty from the administrator's defaults."""
    for k, v in get_defaults(db, generator).items():
        if options.get(k) in (None, ""):
            options[k] = v
    return options
