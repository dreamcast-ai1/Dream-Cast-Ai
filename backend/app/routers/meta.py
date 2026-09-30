from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..generators import GENERATORS, PROJECT_STATUSES, REFERENCE_TYPES
from ..models import ReferenceAsset, User
from ..services import provider_settings, usage

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/generators")
def generators(_: User = Depends(current_user)):
    return [{"id": g.id, "label": g.label, "emoji": g.emoji, "description": g.description,
             "section": g.project_section, "available": False} for g in GENERATORS]


@router.get("/meta")
def meta(_: User = Depends(current_user)):
    s = get_settings()
    return {"project_statuses": PROJECT_STATUSES, "reference_types": REFERENCE_TYPES,
            "max_upload_mb": s.max_upload_mb, "environment": s.app_env}


@router.get("/usage")
def my_usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"period": "today", "items": usage.summary(db, user.id)}


@router.get("/settings/providers")
def provider_status(_: User = Depends(current_user), db: Session = Depends(get_db)):
    """Which providers exist and whether the server has a key for them. Keys are never returned."""
    return {"providers": [{k: v for k, v in p.items() if k not in ("daily_cap", "used_today")} for p in provider_settings.describe(db)],
            "note": "Provider API keys are configured server-side via environment variables."}


@router.get("/settings/storage")
def storage_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    used = db.scalar(select(func.coalesce(func.sum(ReferenceAsset.size_bytes), 0))
                     .where(ReferenceAsset.user_id == user.id)) or 0
    return {"backend": s.storage_backend, "max_upload_mb": s.max_upload_mb, "used_bytes": int(used)}
