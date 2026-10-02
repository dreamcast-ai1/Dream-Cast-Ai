from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..generators import GENERATORS
from ..models import User
from ..providers import ProviderError
from ..schemas import GenerationIn, GenerationOut, RefineIn
from ..security import RateLimiter
from ..services import features, generation, provider_settings, refinement, usage
from ..services.generation_schema import SPECS

router = APIRouter(prefix="/api", tags=["generate"])
refine_rate_limit = RateLimiter(lambda: get_settings().refine_rate_limit_per_minute)


@router.get("/generate/schema")
def schema(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Everything the Create page needs: per-generator fields plus the state of the provider that would run it.
    available=False means a request would be refused (no/disabled/capped provider); configured=False means it would be
    accepted but fail with a configuration message until an administrator sets the provider up."""
    items = []
    for g in GENERATORS:
        if not features.generator_enabled(db, g.id):
            continue                                    # switched off by an administrator: not offered at all
        spec = SPECS[g.id].to_dict()
        for f in spec["fields"]:                        # Hindi/Telugu appear only while an administrator has enabled them
            if f["key"] in ("language", "accent"):
                f["choices"] = features.filter_languages(db, f["choices"])
        available, reason, simulated, configured, config_message, info = True, None, False, True, None, {}
        try:
            p = provider_settings.select_provider(db, g.id, allow_unconfigured=True)
            simulated, configured, info = p.simulated, p.is_configured(), p.info()
            if not configured:
                config_message = p.not_configured_message
            if g.id in ("music", "video") and "durations" in info:
                for f in spec["fields"]:
                    if f["kind"] == "duration":
                        f["choices"] = [str(d) for d in info["durations"]]
                        f["help"] = f"Maximum {max(info['durations'])} seconds"
                    if f["key"] == "aspect_ratio" and info.get("aspect_ratios"):
                        f["choices"] = list(info["aspect_ratios"])
        except ProviderError as e:
            available, reason = False, e.user_message
        items.append({"id": g.id, "label": g.label, "emoji": g.emoji, "description": g.description, "available": available,
                      "unavailable_reason": reason, "simulated": simulated, "configured": configured,
                      "config_message": config_message, "provider_info": info, **spec})
    return {"generators": items, "max_upload_mb": get_settings().max_upload_mb, "max_video_upload_mb": get_settings().max_video_upload_mb}


@router.post("/generate/refine", dependencies=[Depends(refine_rate_limit)])
def refine(body: RefineIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    prep = generation.prepare(db, user, body.generator_type, body.prompt, body.options, body.project_id,
                              body.reference_assets, strict=False)
    flags = features.all_flags(db)
    limit = get_settings().refine_daily_limit
    reason = None
    if not flags["prompt_refinement"]:
        reason = "AI prompt refinement is switched off."
    elif not flags["prefer_ai_refinement"]:
        reason = "Basic refinement is preferred on this site."
    elif limit > 0 and usage.count_refinement(db, user.id) >= limit:
        reason = "Your daily AI prompt-refinement allowance is used up."
    use_llm = reason is None
    # Basic refinement is the free, non-AI mode. It is never AI and is labelled "Basic refinement"; the user is never blocked from generating.
    result = refinement.refine(prep.generator, prep.prompt, prep.options, generation.build_context(db, prep),
                               extra_warnings=prep.warnings, use_llm=use_llm, basic=flags["free_refinement"],
                               fallback_on_error=flags["auto_fallback_refinement"], skip_reason=reason)
    if result.metadata["method"] == "llm":
        usage.record(db, user.id, "refinement", provider=result.metadata["provider"])  # counts real LLM calls only
    return {"refined_prompt": result.refined_prompt, "structured_prompt": result.structured_prompt, "metadata": result.metadata}


@router.post("/generations", response_model=GenerationOut, status_code=201)
def create_generation(body: GenerationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Queues the job and returns immediately; the background worker does the rest."""
    job = generation.submit(db, user, body.generator_type, body.original_prompt, body.refined_prompt, body.options,
                            body.project_id, body.reference_assets, body.parent_id)
    return GenerationOut(job_id=job.id, status=job.status)
