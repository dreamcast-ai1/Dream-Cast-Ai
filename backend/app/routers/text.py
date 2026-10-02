from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import User
from ..security import RateLimiter
from ..services import features, text_studio
from ..services.text_studio import DURATIONS, LANGUAGES, LENGTHS, LIMITS, SCRIPT_FORMATS_LIST

router = APIRouter(prefix="/api/text", tags=["text"])
text_rate_limit = RateLimiter(lambda: get_settings().text_rate_limit_per_minute)


def _clean(v: str | None) -> str:
    return " ".join((v or "").split()) if v else ""


class StoryIn(BaseModel):
    prompt: str = Field(min_length=5, max_length=LIMITS["prompt"])
    genre: str = Field(default="", max_length=LIMITS["genre"])
    tone: str = Field(default="", max_length=LIMITS["tone"])
    length: str = "Medium"
    language: str = Field(default="English", max_length=LIMITS["language"])
    project_id: str | None = Field(default=None, max_length=40)       # optional: save the result in this project

    @field_validator("prompt", mode="before")
    @classmethod
    def _prompt(cls, v):
        v = (v or "").strip() if isinstance(v, str) else v
        if isinstance(v, str) and len(v) < 5:
            raise ValueError("Describe your story in at least a few words.")
        return v

    @field_validator("genre", "tone", "language", mode="before")
    @classmethod
    def _norm(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("length")
    @classmethod
    def _length(cls, v):
        if v not in LENGTHS:
            raise ValueError(f"Length must be one of: {', '.join(LENGTHS)}.")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v):
        if v not in LANGUAGES:
            raise ValueError(f"Language must be one of: {', '.join(LANGUAGES)}.")
        return v


class ScriptIn(BaseModel):
    story: str = Field(min_length=40, max_length=LIMITS["story"])
    style: str = Field(default="", max_length=LIMITS["style"])
    tone: str = Field(default="", max_length=LIMITS["tone"])
    script_format: str = Field(default="", max_length=LIMITS["style"])
    duration_minutes: int | None = None
    language: str = Field(default="English", max_length=LIMITS["language"])
    instructions: str = Field(default="", max_length=LIMITS["instructions"])      # optional notes; the only way to ask for plot changes
    project_id: str | None = Field(default=None, max_length=40)

    @field_validator("story", mode="before")
    @classmethod
    def _story(cls, v):
        v = (v or "").strip() if isinstance(v, str) else v
        if isinstance(v, str) and len(v) < 40:
            raise ValueError("Paste or write the story first (at least a few sentences).")
        return v

    @field_validator("style", "tone", "language", mode="before")
    @classmethod
    def _norm(cls, v):
        return _clean(v) if isinstance(v, str) else v

    @field_validator("script_format")
    @classmethod
    def _format(cls, v):
        v = _clean(v)
        if v and v not in SCRIPT_FORMATS_LIST:
            raise ValueError(f"Script format must be one of: {', '.join(SCRIPT_FORMATS_LIST)}.")
        return v

    @field_validator("duration_minutes")
    @classmethod
    def _duration(cls, v):
        if v is not None and v not in DURATIONS:
            raise ValueError(f"Duration must be one of: {', '.join(map(str, DURATIONS))} minutes.")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v):
        if v not in LANGUAGES:
            raise ValueError(f"Language must be one of: {', '.join(LANGUAGES)}.")
        return v


@router.get("/options")
def options(_: User = Depends(current_user), db: Session = Depends(get_db)):
    """Choices and size limits for the Write page, and whether the text provider is configured (never any key)."""
    return {**text_studio.options(), "languages": features.enabled_languages(db),
            "generators": {g: features.generator_enabled(db, g) for g in ("story", "script")}}


@router.post("/story", dependencies=[Depends(text_rate_limit)])
def create_story(body: StoryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    features.require_generator(db, "story")
    features.check_language(db, body.language)
    return text_studio.story(db, user, prompt=body.prompt, genre=body.genre, tone=body.tone, length=body.length, language=body.language, project_id=body.project_id)


@router.post("/script", dependencies=[Depends(text_rate_limit)])
def create_script(body: ScriptIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    features.require_generator(db, "script")
    features.check_language(db, body.language)
    return text_studio.script(db, user, story_text=body.story, style=body.style, tone=body.tone, script_format=body.script_format, duration=body.duration_minutes, language=body.language,
                              instructions=body.instructions, project_id=body.project_id)
