"""Central configuration. All secrets come from environment variables (.env in dev)."""
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"

INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-me-please-32b"
LOCAL_FRONTEND = "http://localhost:5173"
PLACEHOLDER_PREFIX = "REPLACE_WITH"      # values in .env.example; a copied-but-unfilled placeholder must count as "not set"
MIN_PRODUCTION_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    app_env: str = "development"  # development | production | test
    database_url: str = f"sqlite:///{BACKEND_DIR / 'dreamcast.db'}"
    # Explicit browser origins allowed to call the API (comma separated, exact match, never "*").
    cors_origins: str = "http://localhost:5173"
    # The deployed frontend is always allowed in addition to CORS_ORIGINS, so a missing/stale env var can't break the live site.
    production_frontend_url: str = "https://dreamcaastai.netlify.app"
    frontend_url: str = "http://localhost:5173"
    # Behind a reverse proxy (Render) the real client IP is in X-Forwarded-For. None = trust it only in production.
    trust_proxy_headers: bool | None = None

    # Plan prices in whole rupees per month (INR). Change here (env), not in the frontend. 0 hides nothing: TRAILER is always free.
    plan_indie_price_inr: int = 199
    plan_blockbuster_price_inr: int = 499
    plan_billing_days: int = 30       # how long one successful payment keeps a paid plan active

    # Razorpay (server-side only). Leave empty: the app runs normally and paid checkout answers "payments not configured".
    razorpay_key_id: str = ""         # public key id; the browser needs it to open Checkout
    razorpay_key_secret: str = ""     # SECRET: signs/verifies payments; never sent to the browser or logged
    razorpay_webhook_secret: str = "" # SECRET: verifies webhook calls from Razorpay

    # Auth: "local" (email/password + JWT issued by this API) or "supabase"
    auth_provider: str = "local"
    auth_url: str = ""          # Supabase project URL
    auth_public_key: str = ""   # Supabase anon/publishable key (public by design)
    auth_secret_key: str = INSECURE_DEFAULT_SECRET  # local: JWT signing key; supabase: JWT secret (HS256 projects)
    access_token_minutes: int = 60 * 24 * 7
    admin_emails: str = ""      # comma separated; these accounts get the ADMIN role

    storage_backend: str = "local"
    storage_dir: str = str(ROOT_DIR / "storage")
    max_upload_mb: int = 10

    rate_limit_auth_per_minute: int = 20
    checkout_rate_limit_per_minute: int = 20     # payment orders per IP per minute (0 disables)

    # Prompt-refinement LLM. Any OpenAI-compatible chat API works (Groq, Gemini, OpenRouter, Ollama, OpenAI...).
    llm_provider: str = "groq"      # groq | gemini | openrouter | ollama | custom
    llm_api_key: str = ""
    llm_base_url: str = ""          # optional override; required for "custom"
    llm_model: str = ""             # optional override of the provider's default model
    llm_timeout_seconds: float = 30.0
    refine_daily_limit: int = 40    # prompt refinements (LLM calls) per user per day; 0 = unlimited
    refine_rate_limit_per_minute: int = 10

    # Text generation (story / script / lyrics) reuses the LLM settings above.
    llm_generation_timeout_seconds: float = 120.0
    llm_max_output_tokens: int = 6000   # hard cap per generation call (free tiers usually allow 4-8k)

    # Music provider (external). huggingface = MusicGen through the Hugging Face Inference API (free token available).
    music_provider: str = "huggingface"
    music_api_key: str = ""
    music_model: str = "facebook/musicgen-small"
    music_base_url: str = ""
    music_max_seconds: int = 30         # longest clip the configured provider/model can produce
    music_timeout_seconds: float = 240.0

    # Voice / text-to-speech provider (external). google = Google Cloud Text-to-Speech (free monthly tier).
    voice_provider: str = "google"
    voice_api_key: str = ""
    voice_base_url: str = ""
    voice_timeout_seconds: float = 60.0

    # Video generation (external provider; server-side only). fal = fal.ai queue API (pay-as-you-go).
    video_provider: str = "fal"
    video_provider_api_key: str = ""
    video_provider_model: str = "fal-ai/kling-video/v1.6/standard/text-to-video"
    video_provider_i2v_model: str = "fal-ai/kling-video/v1.6/standard/image-to-video"   # empty = no image-to-video
    video_provider_base_url: str = ""
    video_max_seconds: int = 10             # longest clip the configured model can make (10, 20 or 30). Never exceeded.
    video_aspect_ratios: str = "16:9,9:16,1:1"
    video_poll_seconds: float = 8.0
    video_max_download_mb: int = 300

    # Face replacement (external provider; server-side only).
    face_provider: str = "fal"
    face_provider_api_key: str = ""
    face_provider_model: str = "fal-ai/face-swap"
    face_provider_base_url: str = ""
    face_poll_seconds: float = 5.0

    max_video_upload_mb: int = 100          # face-replacement source videos (images use MAX_UPLOAD_MB)

    # Background jobs
    worker_enabled: bool = True     # run worker threads inside the API process
    worker_concurrency: int = 2
    worker_poll_seconds: float = 2.0
    provider_poll_seconds: float = 2.0
    job_timeout_seconds: int = 900
    job_max_auto_retries: int = 1
    job_retry_delay_seconds: float = 10.0

    # Development simulator: a clearly-labelled fake provider so the job pipeline can be exercised
    # without a real generator. It never produces real media. Keep OFF in production.
    enable_dev_simulator: bool = False
    dev_simulator_step_seconds: float = 1.5

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        """Exact origins only: trailing slashes are dropped (browsers send none) and a wildcard is ignored."""
        configured = [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]
        # Local development origin is always allowed outside production; production only allows what is configured plus the Netlify site.
        local = [] if self.is_production else [LOCAL_FRONTEND]
        origins = [*(configured or local), self.production_frontend_url.strip().rstrip("/"), *local]
        return [o for o in dict.fromkeys(origins) if o and o != "*"]

    @property
    def use_forwarded_for(self) -> bool:
        return self.is_production if self.trust_proxy_headers is None else self.trust_proxy_headers

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_video_upload_bytes(self) -> int:
        return self.max_video_upload_mb * 1024 * 1024

    @model_validator(mode="after")
    def _resolve_paths(self):
        """Relative paths in .env are relative to the project root, regardless of the current directory."""
        if self.database_url.startswith("postgres://"):        # Render/Heroku style URL -> the form SQLAlchemy understands
            self.database_url = "postgresql://" + self.database_url[len("postgres://"):]
        for name in ("auth_secret_key", "auth_public_key", "razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret", "llm_api_key",
                     "music_api_key", "voice_api_key", "video_provider_api_key", "face_provider_api_key"):
            value = getattr(self, name).strip()
            setattr(self, name, "" if value.startswith(PLACEHOLDER_PREFIX) else value)     # an unfilled placeholder is not a credential
        if not self.database_url.strip():
            self.database_url = f"sqlite:///{BACKEND_DIR / 'dreamcast.db'}"
        if not self.auth_secret_key:
            self.auth_secret_key = INSECURE_DEFAULT_SECRET
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix) and not self.database_url.startswith(prefix + "/"):
            rel = self.database_url[len(prefix):]
            if rel != ":memory:":
                self.database_url = prefix + str((ROOT_DIR / rel).resolve())
        if not Path(self.storage_dir).is_absolute():
            self.storage_dir = str((ROOT_DIR / self.storage_dir).resolve())
        return self

    @model_validator(mode="after")
    def _check_production(self):
        # Also signs short-lived media URLs, so a real secret is required in production for every auth provider.
        if self.is_production and self.auth_secret_key == INSECURE_DEFAULT_SECRET:
            raise ValueError("AUTH_SECRET_KEY must be set to a strong random value in production")
        if self.is_production and len(self.auth_secret_key) < MIN_PRODUCTION_SECRET_LENGTH:
            raise ValueError(f"AUTH_SECRET_KEY must be at least {MIN_PRODUCTION_SECRET_LENGTH} characters in production")
        if self.is_production and self.frontend_url.rstrip("/") == LOCAL_FRONTEND:
            self.frontend_url = self.production_frontend_url      # links and redirects must never point at localhost in production
        if self.is_production:
            self.enable_dev_simulator = False       # the fake development provider can never run in production, whatever the env says
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
