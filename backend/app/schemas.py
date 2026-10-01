from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

ReferenceType = Literal["CHARACTER", "LOCATION", "OBJECT", "STYLE", "OTHER"]
ProjectStatus = Literal["DRAFT", "IN_PROGRESS", "COMPLETED", "ARCHIVED"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _strip(v: str) -> str:
    return v.strip() if isinstance(v, str) else v


# ---- auth / user
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class VerifyEmailIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class ResendIn(BaseModel):
    email: EmailStr


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class UserOut(ORM):
    id: str
    email: str
    name: str
    avatar_url: str | None
    auth_provider: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None
    email_verified: bool = True


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    _s = field_validator("name", mode="before")(_strip)


class TokenOut(BaseModel):
    access_token: str
    user: UserOut


# ---- projects
class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    genre: str = Field(default="", max_length=60)
    style: str = Field(default="", max_length=80)        # optional project-wide visual style, e.g. "Cinematic realism"

    _s = field_validator("title", "genre", "style", mode="before")(_strip)


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    genre: str | None = Field(default=None, max_length=60)
    style: str | None = Field(default=None, max_length=80)
    status: ProjectStatus | None = None

    _s = field_validator("title", "genre", mode="before")(_strip)


class ProjectOut(ORM):
    id: str
    title: str
    description: str
    genre: str
    status: str
    thumbnail_url: str | None = None
    style: str = ""
    meta: dict
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    counts: dict[str, int]


# ---- characters
class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    age: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=3000)
    appearance: str = Field(default="", max_length=3000)
    personality: str = Field(default="", max_length=3000)
    clothing: str = Field(default="", max_length=3000)

    _s = field_validator("name", mode="before")(_strip)


class CharacterOut(ORM):
    id: str
    project_id: str
    name: str
    age: str
    description: str
    appearance: str
    personality: str
    clothing: str
    image_url: str | None = None
    created_at: datetime
    updated_at: datetime


# ---- references
class ReferenceOut(ORM):
    id: str
    project_id: str
    type: str
    name: str
    original_filename: str
    mime_type: str
    size_bytes: int
    url: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    created_at: datetime


# ---- assets / jobs / notifications
class AssetOut(ORM):
    id: str
    project_id: str
    type: str
    title: str
    text_preview: str | None = None
    prompt: str
    provider: str | None
    status: str
    version: int = 1
    lineage_id: str | None = None
    format: str = ""
    language: str = ""
    mime_type: str = ""
    duration_seconds: float | None = None
    meta: dict
    job_id: str | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    has_file: bool = False
    created_at: datetime
    updated_at: datetime


class LibraryItem(AssetOut):
    """An asset in the user's library (all projects). is_movie marks assembled movies, which are stored as videos."""
    project_title: str | None = None
    is_movie: bool = False


class AssetVersion(BaseModel):
    id: str
    version: int
    title: str
    created_at: datetime


class AssetDetail(AssetOut):
    text_content: str | None = None
    versions: list[AssetVersion] = []


class AssetPatch(BaseModel):
    text_content: str | None = Field(default=None, max_length=200000)
    title: str | None = Field(default=None, min_length=1, max_length=200)


class JobAsset(BaseModel):
    id: str
    type: str
    status: str
    text_preview: str | None = None
    url: str | None = None
    thumbnail_url: str | None = None


class JobOut(ORM):
    id: str
    type: str
    status: str
    stage: str
    progress: int | None
    project_id: str | None
    project_title: str | None = None
    original_prompt: str
    refined_prompt: str
    options: dict
    reference_assets: list
    provider: str | None
    simulated: bool = False
    error_code: str | None
    error_message: str | None
    attempts: int
    parent_id: str | None
    cancel_requested: bool
    output: dict = {}
    assets: list[JobAsset] = []
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class NotificationOut(ORM):
    id: str
    type: str
    title: str
    message: str
    is_read: bool
    job_id: str | None
    project_id: str | None
    asset_id: str | None = None
    created_at: datetime


# ---- admin
class AdminUserPatch(BaseModel):
    is_active: bool | None = None
    role: Literal["USER", "ADMIN"] | None = None


class LimitsIn(BaseModel):
    limits: dict[str, int]
    plan: str = "trailer"


class SubscriptionPatch(BaseModel):
    plan_id: str
    days: int | None = Field(default=None, ge=1, le=3650)     # None = no expiry


# ---- generation engine
class RefineIn(BaseModel):
    generator_type: str
    prompt: str = Field(default="", max_length=10000)
    options: dict = Field(default_factory=dict)
    project_id: str | None = None
    reference_assets: list[str] = Field(default_factory=list)


class GenerationIn(BaseModel):
    generator_type: str
    original_prompt: str = Field(default="", max_length=10000)
    refined_prompt: str = Field(default="", max_length=20000)
    options: dict = Field(default_factory=dict)
    project_id: str | None = None
    reference_assets: list[str] = Field(default_factory=list)
    parent_id: str | None = None


class GenerationOut(BaseModel):
    job_id: str
    status: str
    message: str = "Generation started."


class ProviderPatch(BaseModel):
    enabled: bool | None = None
    daily_cap: int | None = Field(default=None, ge=0, le=100000)
    clear_cap: bool = False
