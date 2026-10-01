"""Executes one claimed job: provider selection -> generate -> poll -> store result -> notify."""
import logging
import time

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import GenerationJob, ReferenceAsset
from ..providers import ErrorCode, GenerationRequest, Provider, ProviderError, ProviderResult
from ..providers.base import SyncProvider
from . import assets, jobs, movie, provider_settings

log = logging.getLogger("dreamcast.runner")


def build_request(db: Session, job: GenerationJob) -> GenerationRequest:
    refs = []
    if job.reference_assets:
        for r in db.query(ReferenceAsset).filter(ReferenceAsset.id.in_(job.reference_assets)).all():
            refs.append({"id": r.id, "name": r.name, "type": r.type, "path": r.file_path, "mime_type": r.mime_type})
    return GenerationRequest(id=job.id, user_id=job.user_id, project_id=job.project_id, generator_type=job.type,
                             original_prompt=job.original_prompt, refined_prompt=job.refined_prompt, options=job.options,
                             reference_assets=refs, context=job.context, provider=job.provider, status=job.status,
                             attempt=job.attempts, created_at=job.created_at, updated_at=job.updated_at)


def _store_result(db: Session, job: GenerationJob, result: ProviderResult) -> dict:
    """Saves the provider's output. Without a project the result stays on the job (assets always belong to a project)."""
    meta = {**result.meta, "simulated": result.simulated}
    if not job.project_id:
        return {**meta, "text": result.text}
    try:
        asset = assets.create_from_result(db, job, result)
    except ProviderError:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("Could not save result of job %s", job.id)
        raise ProviderError(ErrorCode.STORAGE_ERROR, str(e))
    return {**meta, "asset_id": asset.id, "version": asset.version}


def run_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        if not job or job.status != "PROCESSING":
            return
        try:
            _execute(db, job)
        except ProviderError as e:
            db.refresh(job)
            jobs.handle_error(db, job, e)
        except Exception:  # noqa: BLE001 - never let a job crash the worker; details stay in the log
            log.exception("Unexpected error in job %s", job_id)
            db.rollback()
            db.refresh(job)
            jobs.fail(db, job, ProviderError(ErrorCode.UNKNOWN_ERROR, "unexpected error"))


def _execute(db: Session, job: GenerationJob) -> None:
    s = get_settings()
    if job.cancel_requested:
        return jobs.cancel_now(db, job)
    if job.type == movie.MOVIE_JOB:           # joins existing clips with FFmpeg: no AI provider involved
        return movie.run_assembly(db, job)
    provider: Provider = provider_settings.select_provider(db, job.type)
    job.provider = provider.name
    db.commit()
    if job.external_id and job.reached_provider and provider.resumable:
        ext = job.external_id       # worker restarted mid-job: keep polling the existing provider job (never resubmit and pay twice)
        jobs.set_stage(db, job, "GENERATING")
    else:
        # One-call providers (LLM, TTS...) do their real work inside generate(), so that IS the provider-processing stage;
        # queue-style providers (video, face) first submit and then are polled.
        jobs.set_stage(db, job, "GENERATING" if isinstance(provider, SyncProvider) else "SUBMITTING")
        request = build_request(db, job)
        ext = provider.generate(request)
        job.external_id, job.reached_provider = ext, True
        db.commit()
        jobs.set_stage(db, job, "GENERATING")    # provider processing
    poll = provider.poll_seconds or s.provider_poll_seconds
    deadline = time.monotonic() + s.job_timeout_seconds
    while True:
        db.refresh(job)
        if job.cancel_requested:
            ok = provider.cancel(ext) if provider.supports_cancel else False
            return jobs.cancel_now(db, job, provider_cancelled=ok)
        st = provider.get_status(ext)
        if st.state == "COMPLETED":
            result = st.result
            if result is None:                   # output lives at the provider: stream it into our own storage
                jobs.set_stage(db, job, "DOWNLOADING")
                result = provider.download_result(ext, build_request(db, job))
            jobs.set_stage(db, job, "STORING")
            out = _store_result(db, job, result)
            return jobs.complete(db, job, out, units=result.units, cost_estimate=result.cost_estimate)
        if st.state == "FAILED":
            raise st.error or ProviderError(ErrorCode.GENERATION_FAILED, "provider reported failure")
        if job.stage != st.stage or (st.progress is not None and st.progress != job.progress):
            jobs.set_stage(db, job, st.stage, st.progress)
        if time.monotonic() > deadline:
            if provider.supports_cancel:
                provider.cancel(ext)             # best effort; never claimed as confirmed
            raise ProviderError(ErrorCode.GENERATION_FAILED, "timed out", message="The provider took too long to finish this generation, so it was stopped. You can retry it.")
        time.sleep(poll)
