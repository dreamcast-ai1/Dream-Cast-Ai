from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import GeneratedAsset, GenerationJob, Project
from ..providers import registry
from ..schemas import JobAsset, JobOut


def job_out(db: Session, job: GenerationJob) -> JobOut:
    out = JobOut.model_validate(job)
    out.output = {k: v for k, v in (job.output_meta or {}).items() if k != "text"} | ({"text": job.output_meta["text"]} if "text" in (job.output_meta or {}) else {})
    if job.project_id:
        p = db.get(Project, job.project_id)
        out.project_title = p.title if p else None
    prov = registry.get(job.provider) if job.provider else None
    out.simulated = bool(prov and prov.simulated) or bool((job.output_meta or {}).get("simulated"))
    assets = db.scalars(select(GeneratedAsset).where(GeneratedAsset.job_id == job.id)).all()
    out.assets = [JobAsset(id=a.id, type=a.type, status=a.status, url=f"/api/files/asset/{a.id}" if a.file_path else None,
                           thumbnail_url=f"/api/files/thumbnail/{a.id}" if (a.meta or {}).get("thumbnail") else None,
                           text_preview=(a.text_content or "")[:600] or None) for a in assets]
    return out
