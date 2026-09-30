from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..errors import NotFound
from ..generators import canonical_generator
from ..models import GenerationJob, User
from ..schemas import GenerationOut, JobOut
from ..services import generation, jobs as job_service
from ..services.job_views import job_out

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _own(db: Session, user: User, job_id: str) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job or job.user_id != user.id:
        raise NotFound("Generation not found.")
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = 30, offset: int = 0, project_id: str | None = None, type: str | None = None,
              status: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """type: comma-separated generator ids (aliases like FACE/AVATAR accepted). status: comma-separated."""
    q = select(GenerationJob).where(GenerationJob.user_id == user.id)
    if project_id:
        q = q.where(GenerationJob.project_id == project_id)
    if type:
        types = [canonical_generator(t) for t in type.split(",")]
        q = q.where(GenerationJob.type.in_([t for t in types if t]))
    if status:
        q = q.where(GenerationJob.status.in_([s.strip().upper() for s in status.split(",")]))
    rows = db.scalars(q.order_by(GenerationJob.created_at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 100))).all()
    return [job_out(db, j) for j in rows]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return job_out(db, _own(db, user, job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return job_out(db, job_service.request_cancel(db, _own(db, user, job_id)))


@router.post("/{job_id}/regenerate", response_model=GenerationOut, status_code=201)
def regenerate(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Regenerate (or manually retry) with the same settings. Creates a NEW linked job; the original is untouched.
    To change the prompt/options first, use Create with ?from=<job id> and submit with parent_id."""
    old = _own(db, user, job_id)
    job = generation.submit(db, user, old.type, old.original_prompt, old.refined_prompt, old.options, old.project_id,
                            old.reference_assets, parent_id=old.id)
    return GenerationOut(job_id=job.id, status=job.status)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job_service.delete_job(db, _own(db, user, job_id))
