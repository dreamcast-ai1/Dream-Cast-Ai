from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user
from ..errors import AppError, NotFound
from ..generators import GENERATORS
from ..models import GenerationJob, UsageRecord, User
from ..providers import registry
from ..schemas import AdminUserPatch, JobOut, LimitsIn, ProviderPatch, UserOut
from ..services import provider_settings, usage

# Every route in this router requires an ADMIN, enforced server-side.
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_user)])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    count = lambda q: db.scalar(q) or 0
    return {
        "total_users": count(select(func.count()).select_from(User)),
        "active_users": count(select(func.count()).select_from(User).where(User.is_active.is_(True))),
        "total_generations": count(select(func.count()).select_from(GenerationJob)),
        "failed_generations": count(select(func.count()).select_from(GenerationJob).where(GenerationJob.status == "FAILED")),
        "api_usage": count(select(func.coalesce(func.sum(UsageRecord.request_count), 0))),
        "by_generator": {g: n for g, n in db.execute(
            select(UsageRecord.generator_type, func.coalesce(func.sum(UsageRecord.request_count), 0))
            .group_by(UsageRecord.generator_type)).all()},
    }


@router.get("/users")
def users(db: Session = Depends(get_db)):
    gens = dict(db.execute(select(GenerationJob.user_id, func.count()).group_by(GenerationJob.user_id)).all())
    reqs = dict(db.execute(select(UsageRecord.user_id, func.coalesce(func.sum(UsageRecord.request_count), 0))
                           .group_by(UsageRecord.user_id)).all())
    rows = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [{**UserOut.model_validate(u).model_dump(), "generations": gens.get(u.id, 0), "requests": reqs.get(u.id, 0)}
            for u in rows]


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(user_id: str, body: AdminUserPatch, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise NotFound("User not found.")
    if target.id == admin.id and (body.is_active is False or body.role == "USER"):
        raise AppError("You cannot disable or demote your own account.", 400, "self_lockout")
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.role is not None:
        target.role = body.role
    db.commit()
    return target


@router.get("/limits")
def get_limits(db: Session = Depends(get_db)):
    limits = usage.get_limits(db)
    return {"items": [{"generator": g.id, "label": g.label, "emoji": g.emoji, "limit": limits[g.id]} for g in GENERATORS]}


@router.put("/limits")
def put_limits(body: LimitsIn, db: Session = Depends(get_db)):
    if any(v < 0 or v > 100000 for v in body.limits.values()):
        raise AppError("Limits must be between 0 and 100000.", 422, "validation_error")
    usage.set_limits(db, body.limits)
    return get_limits(db)


@router.get("/jobs", response_model=list[JobOut])
def jobs(status: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    """Newest first. Admin view; user-facing prompts/options are included for support purposes."""
    q = select(GenerationJob).order_by(GenerationJob.created_at.desc()).limit(min(max(limit, 1), 200))
    if status:
        q = q.where(GenerationJob.status == status.upper())
    from ..services.job_views import job_out
    return [job_out(db, j) for j in db.scalars(q).all()]


@router.get("/providers")
def providers(db: Session = Depends(get_db)):
    return {"providers": provider_settings.describe(db)}


@router.put("/providers/{name}")
def update_provider(name: str, body: ProviderPatch, db: Session = Depends(get_db)):
    if not registry.get(name):
        raise NotFound("Provider not found.")
    cap = None if body.clear_cap else (body.daily_cap if body.daily_cap is not None else "keep")
    provider_settings.update(db, name, enabled=body.enabled, daily_cap=cap)
    return {"providers": provider_settings.describe(db)}
