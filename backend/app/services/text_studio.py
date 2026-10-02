"""Synchronous Story and Story-to-Script generation for the Write page. It adds NO new generation machinery:
 - the prompts come from providers/text_formats.py (the same builders the queued Story/Script generators use),
 - the LLM call goes through the existing provider (PromptRefinementProvider.generate_text), so Gemini or Groq is chosen by LLM_PROVIDER,
 - the daily allowance is the existing per-generator limit ("story" / "script") and usage records,
 - saving to a project creates an ordinary asset (and a History entry), exactly like a finished queued job.
The text provider key is only ever read on the server."""
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AppError
from ..generators import BY_ID
from ..models import GenerationJob, User
from ..providers import ErrorCode, ProviderError, ProviderResult
from ..providers.text import PromptRefinementProvider
from ..providers.text_formats import LANGUAGE_NOTES, SCRIPT_FORMATS, STUDIO_STORY_WORDS, build_script_json, build_story_json
from ..textparse import word_count
from . import assets, generation, usage
from .generation_schema import GENRES
from .jobs import _QUOTA_LOCK

log = logging.getLogger("dreamcast.text")

TONES = ["Dramatic", "Light-hearted", "Humorous", "Dark", "Suspenseful", "Romantic", "Inspirational", "Melancholic", "Epic"]
SCRIPT_FORMATS_LIST = list(SCRIPT_FORMATS)
SCRIPT_STYLES = ["Cinematic", "Dialogue-driven", "Visual / minimal dialogue", "Documentary", "Comedy", "Thriller", "Animated short"]
DURATIONS = [5, 10, 20, 30]
LENGTHS = list(STUDIO_STORY_WORDS)
LANGUAGES = list(LANGUAGE_NOTES)
LIMITS = {"prompt": 2000, "story": 20000, "instructions": 1000, "genre": 60, "tone": 60, "style": 80, "language": 30}
NOT_CONFIGURED = "Text generation isn't set up yet: the text provider is not configured. An administrator needs to set LLM_PROVIDER and LLM_API_KEY."
STUDIO_TIMEOUT_CAP = 90.0        # seconds; keeps the request inside typical proxy limits


def options() -> dict:
    return {"genres": [g for g in GENRES if g != "Custom"], "tones": TONES, "languages": LANGUAGES, "lengths": LENGTHS, "script_styles": SCRIPT_STYLES, "script_formats": SCRIPT_FORMATS_LIST,
            "durations": DURATIONS, "limits": LIMITS, "configured": PromptRefinementProvider().is_configured()}


def _llm() -> PromptRefinementProvider:
    llm = PromptRefinementProvider()
    if not llm.is_configured():
        raise AppError(NOT_CONFIGURED, 503, "text_provider_not_configured")
    return llm


def provider_error(e: ProviderError) -> AppError:
    """Provider failures become clear, safe messages (never the provider's own response, never a key)."""
    detail = (e.detail or "").lower()
    if e.code == ErrorCode.API_NOT_CONFIGURED:
        return AppError(NOT_CONFIGURED, 503, "text_provider_not_configured")
    if e.code == ErrorCode.AUTHENTICATION_ERROR:
        return AppError("The text provider rejected its API key. An administrator needs to check LLM_API_KEY.", 502, "text_provider_auth")
    if e.code == ErrorCode.RATE_LIMITED:
        return AppError("The text provider is rate-limiting requests right now. Please try again in a minute.", 429, "text_provider_rate_limited")
    if e.code == ErrorCode.PROVIDER_UNAVAILABLE:
        if detail.startswith("timeout"):
            return AppError("The text provider took too long to answer. Please try again, or use a shorter request.", 504, "text_provider_timeout")
        return AppError("The text provider is temporarily unavailable. Please try again in a few minutes.", 503, "text_provider_unavailable")
    if e.code == ErrorCode.INVALID_REQUEST:
        return AppError("The text provider couldn't process this request. Try shorter or different text.", 502, "text_provider_rejected")
    return AppError("The text provider returned an unusable answer. Please try again.", 502, "text_provider_bad_response")


def _reserve(db: Session, user: User, generator: str, provider: str):
    """Check the daily allowance and take one unit, as one step (same allowance as the queued generators)."""
    with _QUOTA_LOCK:
        if not usage.has_quota(db, user.id, generator):
            raise AppError(f"You've reached today's {BY_ID[generator].label} limit. It resets at midnight UTC.", 429, "QUOTA_EXCEEDED")
        rec = usage.reserve(db, user.id, generator, None, provider)
        db.commit()
    return rec


BAD_FORMAT = "The text provider's answer wasn't in the expected format. Please try again."


def parse_json_object(raw: str) -> dict:
    """The model's answer as a JSON object. Tolerates a code fence or a sentence around it; anything else is a malformed response."""
    t = (raw or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    data = json.loads(t[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _str(v, limit: int = 20000) -> str:
    """A trimmed string from a JSON value (numbers become text; lists, dicts and booleans become empty)."""
    return str(v).strip()[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def _characters(v) -> list[dict]:
    out = []
    for c in v if isinstance(v, list) else []:
        if isinstance(c, dict) and _str(c.get("name"), 80):
            out.append({"name": _str(c.get("name"), 80), "description": _str(c.get("description"), 400)})
        elif isinstance(c, str) and c.strip():
            out.append({"name": c.strip()[:80], "description": ""})
    return out[:30]


def _validate_story(d: dict) -> dict:
    full = _str(d.get("full_story"), 30000)
    title = _str(d.get("title"), 120)
    if not title or len(full) < 40:
        raise ValueError("story is missing a title or the full story")
    return {"title": title, "logline": _str(d.get("logline"), 300), "setting": _str(d.get("setting"), 600), "characters": _characters(d.get("characters")),
            "beginning": _str(d.get("beginning"), 2000), "middle": _str(d.get("middle"), 2000), "climax": _str(d.get("climax"), 2000),
            "ending": _str(d.get("ending"), 2000), "full_story": full}


def _estimate_seconds(scene: dict) -> int:
    """If the model gave a sensible number use it; otherwise estimate from how much has to be said and shown (about 2.5 words a second)."""
    given = scene.get("estimated_seconds")
    if isinstance(given, (int, float)) and not isinstance(given, bool) and 2 <= given <= 900:
        return int(round(given))
    words = word_count(" ".join([scene["action"], scene["narration"]] + [d["line"] for d in scene["dialogue"]]))
    return max(5, int(round(words / 2.5)))


def _validate_script(d: dict) -> dict:
    scenes = []
    for i, sc in enumerate(d.get("scenes") if isinstance(d.get("scenes"), list) else [], start=1):
        if not isinstance(sc, dict):
            continue
        dialogue = [{"speaker": _str(x.get("speaker"), 60).title(), "line": _str(x.get("line"), 1200)} for x in (sc.get("dialogue") if isinstance(sc.get("dialogue"), list) else [])
                    if isinstance(x, dict) and _str(x.get("speaker"), 60) and _str(x.get("line"), 1200)]
        location, time = _str(sc.get("location"), 120), _str(sc.get("time"), 60)
        heading = _str(sc.get("heading"), 160).upper() or (f"{location} - {time}".strip(" -").upper() if location else "")
        if heading and not re.match(r"^(INT|EXT|INT\./EXT|I/E)[. ]", heading):
            heading = f"INT. {heading}"                       # the rest of the app recognises scenes by their INT./EXT. slug line
        scene = {"number": i, "heading": heading, "location": location, "time": time, "action": _str(sc.get("action"), 4000), "narration": _str(sc.get("narration"), 2000),
                 "dialogue": dialogue, "sound": _str(sc.get("sound"), 600), "camera": _str(sc.get("camera"), 600), "transition": _str(sc.get("transition"), 40).upper(),
                 "estimated_seconds": sc.get("estimated_seconds")}
        if not scene["action"] and not dialogue and not scene["narration"]:
            continue
        scene["number"] = len(scenes) + 1
        scene["estimated_seconds"] = _estimate_seconds(scene)         # the model's own number when it is sensible, otherwise an estimate
        scenes.append(scene)
    title = _str(d.get("title"), 120)
    if not title or not scenes:
        raise ValueError("script is missing a title or scenes")
    return {"title": title, "logline": _str(d.get("logline"), 300), "characters": _characters(d.get("characters")), "scenes": scenes}


def _story_text(s: dict) -> str:
    """The story as plain text in the format the rest of the app already understands (editable, usable as script input, ACT sections for video context)."""
    lines = [f"TITLE: {s['title']}", f"LOGLINE: {s['logline']}", f"SETTING: {s['setting']}", "MAIN CHARACTERS:"]
    lines += [f"- {c['name']}: {c['description']}" for c in s["characters"]]
    lines += ["STORY OUTLINE", f"ACT 1: {s['beginning']}", f"ACT 2: {s['middle']}", f"ACT 3: {s['climax']}", f"ENDING: {s['ending']}", "", "FULL STORY", s["full_story"]]
    return "\n".join(lines)


def _script_text(sc: dict) -> str:
    """The script in the project's screenplay format (so scene detection, 'From script' and video prompts keep working on saved scripts)."""
    out = [f"TITLE: {sc['title']}", f"LOGLINE: {sc['logline']}", "CHARACTERS:"] + [f"- {c['name'].upper()}: {c['description']}" for c in sc["characters"]] + ["SCENE LIST:"]
    out += [f"{s['number']}. {s['heading'] or 'SCENE'}: {(s['action'] or s['narration'])[:90]}" for s in sc["scenes"]]
    for s in sc["scenes"]:
        out += ["", f"SCENE {s['number']:02d}", s["heading"] or "INT. LOCATION - DAY"]
        out += ["Action:", s["action"]] if s["action"] else []
        out += [f"Narration: {s['narration']}"] if s["narration"] else []
        out += [f"Camera: {s['camera']}"] if s["camera"] else []
        out += [f"Sound: {s['sound']}"] if s["sound"] else []
        out += [f"Duration: about {s['estimated_seconds']} seconds"]
        if s["dialogue"]:
            out.append("DIALOGUE")
            for d in s["dialogue"]:
                out += [f"{d['speaker'].upper()}:", f'"{d["line"]}"']
        out += [f"Transition: {s['transition']}"] if s["transition"] else []
    return "\n".join(out + ["END"])


def _complete_json(db: Session, user: User, generator: str, job, validate):
    """One allowance unit covers the whole request. The model is asked for JSON; a malformed answer gets one more try before it counts as a failure."""
    llm = _llm()
    rec = _reserve(db, user, generator, llm.name)
    s = get_settings()
    last = "unparseable"
    try:
        for attempt in (1, 2):
            try:
                raw = llm.generate_text(job.system, job.user, max_tokens=job.max_tokens, temperature=0.8, json_mode=True,
                                        timeout=min(s.llm_generation_timeout_seconds, STUDIO_TIMEOUT_CAP))
            except ProviderError as e:
                log.warning("studio %s failed: %s (%s)", generator, e.code.value, (e.detail or "")[:80])
                raise provider_error(e)
            try:
                result = validate(parse_json_object(raw))
                rec.status, rec.units = "SUCCEEDED", float(word_count(raw))
                db.commit()
                return result, llm, rec
            except (ValueError, TypeError, KeyError) as e:          # json.JSONDecodeError is a ValueError
                last = type(e).__name__
                log.warning("studio %s: malformed answer on attempt %s (%s)", generator, attempt, last)
        raise AppError(BAD_FORMAT, 502, "text_provider_bad_response")
    except AppError:
        rec.status, rec.request_count = "REFUNDED", 0                    # a failed request never costs allowance
        db.commit()
        raise


def _save(db: Session, user: User, project, kind: str, prompt: str, opts: dict, text: str, title: str | None, language: str, llm, rec) -> dict | None:
    """Stores the result as a normal project asset (versioned, editable, shown in the Library and History). `project` was already checked to be the user's."""
    if project is None:
        return None
    now = datetime.now(timezone.utc)
    job = GenerationJob(user_id=user.id, project_id=project.id, type=kind, status="COMPLETED", stage="COMPLETED", original_prompt=prompt[:4000], refined_prompt=prompt[:8000],
                        options=opts, provider=f"{llm.name}-text", started_at=now, completed_at=now, input_meta={"studio": True})
    db.add(job)
    db.flush()
    asset = assets.create_from_result(db, job, ProviderResult(text=text, title=title, language=language, meta={"model": llm.model, "language": language}))
    job.output_meta = {"asset_id": asset.id, "version": asset.version}
    rec.job_id = job.id
    db.commit()
    return {"asset_id": asset.id, "project_id": project.id, "job_id": job.id}


def _tail(db: Session, user: User, generator: str, llm) -> dict:
    return {"model": llm.model, "remaining": usage.remaining(db, user.id, generator)}


# ------------------------------------------------------------------ Prompt -> Story
def story(db: Session, user: User, *, prompt: str, genre: str, tone: str, length: str, language: str, project_id: str | None) -> dict:
    project = generation.owned_project_or_404(db, user, project_id)      # refuse a foreign/unknown project BEFORE calling the provider or spending allowance
    opts = {"genre": genre, "tone": tone, "length": length, "language": language}
    job = build_story_json(prompt, opts, {}, get_settings().llm_max_output_tokens)
    st, llm, rec = _complete_json(db, user, "story", job, _validate_story)
    text = _story_text(st)
    saved = _save(db, user, project, "story", prompt, opts, text, st["title"], language, llm, rec)
    return {"kind": "story", **st, "text": text, "word_count": word_count(st["full_story"]), "language": language, "genre": genre, "tone": tone, "saved": saved,
            **_tail(db, user, "story", llm)}


# ------------------------------------------------------------------ Story -> Script
def script(db: Session, user: User, *, story_text: str, style: str, tone: str, script_format: str, duration: int | None, language: str, instructions: str,
           project_id: str | None) -> dict:
    project = generation.owned_project_or_404(db, user, project_id)
    opts = {"style": style, "tone": tone, "script_format": script_format or "Screenplay", "language": language}
    if duration:
        opts["target_minutes"] = f"{duration} minutes"
    job = build_script_json(instructions, opts, {"story_full": story_text}, get_settings().llm_max_output_tokens)
    sc, llm, rec = _complete_json(db, user, "script", job, _validate_script)
    text = _script_text(sc)
    saved = _save(db, user, project, "script", instructions or "Story to script", {**opts, "story_chars": len(story_text)}, text, sc["title"], language, llm, rec)
    return {"kind": "script", **sc, "scene_count": len(sc["scenes"]), "estimated_total_seconds": sum(x["estimated_seconds"] for x in sc["scenes"]), "text": text,
            "word_count": word_count(text), "language": language, "style": style, "tone": tone, "script_format": opts["script_format"], "duration_minutes": duration,
            "saved": saved, **_tail(db, user, "script", llm)}
