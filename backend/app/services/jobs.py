"""Job lifecycle service (DB-backed queue). The runner/worker call these; routers expose them to users."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..errors import AppError
from ..generators import ACTIVE_STATUSES, BY_ID
from ..models import GenerationJob
from ..providers import REFUNDABLE, ErrorCode, ProviderError
from . import notifications, usage

log = logging.getLogger("dreamcast.jobs")
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _now():
    return datetime.now(timezone.utc)


def create_job(db: Session, user_id: str, generator: str, *, project_id: str | None = None, original_prompt: str = "",
               refined_prompt: str = "", options: dict | None = None, reference_assets: list | None = None,
               context: dict | None = None, provider: str | None = None, parent_id: str | None = None,
               input_meta: dict | None = None) -> GenerationJob:
    """Low-level: checks the daily allowance, creates a QUEUED job and reserves one unit of usage."""
    if generator not in BY_ID:
        raise AppError("Unknown generator type.", 400)
    if not usage.has_quota(db, user_id, generator):
        raise AppError("You have reached today's limit for this generator.", 429, ErrorCode.QUOTA_EXCEEDED.value)
    job = GenerationJob(user_id=user_id, project_id=project_id, type=generator, original_prompt=original_prompt,
                        refined_prompt=refined_prompt, options=options or {}, reference_assets=reference_assets or [],
                        context=context or {}, provider=provider, parent_id=parent_id, input_meta=input_meta or {})
    db.add(job)
    db.flush()
    usage.reserve(db, user_id, generator, job.id, provider)
    db.commit()
    return job


def claim_next(db: Session) -> GenerationJob | None:
    """Atomically move one due job to PROCESSING (safe with several worker threads/processes)."""
    now = _now()
    candidates = db.scalars(select(GenerationJob).where(
        (GenerationJob.status == "QUEUED") | ((GenerationJob.status == "RETRYING") & (GenerationJob.retry_at <= now)))
        .order_by(GenerationJob.created_at).limit(5)).all()
    for job in candidates:
        res = db.execute(update(GenerationJob).where(GenerationJob.id == job.id, GenerationJob.status == job.status)
                         .values(status="PROCESSING", stage="PREPARING", attempts=GenerationJob.attempts + 1,
                                 started_at=job.started_at or now, updated_at=now, retry_at=None))
        db.commit()
        if res.rowcount == 1:
            db.refresh(job)
            return job
    return None


def recover_interrupted(db: Session) -> int:
    """After a crash/restart: jobs stuck in PROCESSING go back to the queue (or fail after repeated interruptions)."""
    stuck = db.scalars(select(GenerationJob).where(GenerationJob.status == "PROCESSING")).all()
    for job in stuck:
        if job.cancel_requested:
            cancel_now(db, job, local_only=True)
        elif job.attempts >= 4:
            fail(db, job, ProviderError(ErrorCode.UNKNOWN_ERROR, "job interrupted repeatedly"))
        else:
            # external_id is kept: resumable providers (video) continue polling instead of resubmitting.
            job.status, job.stage = "QUEUED", "QUEUED"
            db.commit()
    return len(stuck)


def set_stage(db: Session, job: GenerationJob, stage: str, progress: int | None = None) -> None:
    job.stage = stage
    if progress is not None:
        job.progress = max(0, min(100, progress))
    db.commit()


def complete(db: Session, job: GenerationJob, output_meta: dict | None = None, *, units: float = 0.0,
             cost_estimate: float = 0.0) -> None:
    job.status = job.stage = "COMPLETED"
    job.completed_at, job.error_code, job.error_message = _now(), None, None
    job.output_meta = output_meta or {}
    db.commit()
    usage.settle(db, job.id, refund=False, provider=job.provider, units=units, cost_estimate=cost_estimate)
    ready = {"video": "Your video is ready.", "face_replacement": "Your face replacement is ready."}.get(
        job.type, f"Your {BY_ID[job.type].label.lower()} generation is ready.")
    notifications.notify(db, job.user_id, ready, (job.original_prompt or "")[:120], type="job_completed", job_id=job.id,
                         project_id=job.project_id, asset_id=(output_meta or {}).get("asset_id"))


def fail(db: Session, job: GenerationJob, error: ProviderError) -> None:
    job.status = job.stage = "FAILED"
    job.completed_at, job.error_code, job.error_message = _now(), error.code.value, error.user_message
    db.commit()
    refund = (not job.reached_provider) or error.code in REFUNDABLE
    usage.settle(db, job.id, refund=refund, provider=job.provider, status="FAILED")
    notifications.notify(db, job.user_id, f"Your {BY_ID[job.type].label.lower()} generation failed.", error.user_message,
                         type="job_failed", job_id=job.id, project_id=job.project_id)


def schedule_retry(db: Session, job: GenerationJob, error: ProviderError) -> None:
    job.status = job.stage = "RETRYING"
    job.error_code, job.external_id = error.code.value, None
    job.retry_at = _now() + timedelta(seconds=get_settings().job_retry_delay_seconds)
    db.commit()
    notifications.notify(db, job.user_id, f"Your {BY_ID[job.type].label.lower()} generation is being retried.",
                         "A temporary problem occurred. We'll try once more automatically.", type="job_retry",
                         job_id=job.id, project_id=job.project_id)


def handle_error(db: Session, job: GenerationJob, error: ProviderError) -> None:
    log.warning("job %s attempt %s failed: %s (%s)", job.id, job.attempts, error.code.value, error.detail)
    if error.transient and job.attempts <= get_settings().job_max_auto_retries:
        schedule_retry(db, job, error)
    else:
        fail(db, job, error)


def cancel_now(db: Session, job: GenerationJob, *, local_only: bool = False, provider_cancelled: bool = False) -> None:
    job.status = job.stage = "CANCELLED"
    job.completed_at = _now()
    if job.reached_provider:
        job.output_meta = {**job.output_meta, "cancellation": "provider_cancelled" if provider_cancelled else "stopped_locally"}
    db.commit()
    usage.settle(db, job.id, refund=not job.reached_provider, provider=job.provider, status="CANCELLED")


def request_cancel(db: Session, job: GenerationJob) -> GenerationJob:
    """User-facing cancel. Queued jobs are cancelled immediately; running jobs are flagged and the runner stops them."""
    if job.status in TERMINAL:
        raise AppError("This generation has already finished.", 409, "job_finished")
    if job.status in ("QUEUED", "RETRYING"):
        cancel_now(db, job)
    else:
        job.cancel_requested = True
        db.commit()
    return job


def delete_job(db: Session, job: GenerationJob) -> None:
    if job.status in ACTIVE_STATUSES:
        raise AppError("Cancel this generation before deleting it.", 409, "job_active")
    db.delete(job)
    db.commit()


def new_session() -> Session:
    return SessionLocal()
