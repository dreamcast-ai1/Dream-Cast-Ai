"""Admin-only readiness summary: which external services are configured on THIS server, without ever returning a secret value.
Booleans, names and non-secret settings only."""
from urllib.parse import urlparse

from ..config import INSECURE_DEFAULT_SECRET, MIN_PRODUCTION_SECRET_LENGTH, get_settings
from ..email import get_email_provider
from ..storage import get_storage


def _db_kind(url: str) -> str:
    return urlparse(url).scheme.split("+")[0] or "unknown"


def razorpay_mode(key_id: str) -> str:
    """test / live, read from the PUBLIC key id prefix (never from the secret)."""
    return "test" if key_id.startswith("rzp_test_") else "live" if key_id.startswith("rzp_live_") else ("unknown" if key_id else "not set")


def _storage_item(s) -> dict:
    if s.storage_backend == "s3":
        ok, message = get_storage().ping()
        host = urlparse(s.s3_endpoint_url).netloc or "AWS S3"
        return {"id": "storage", "label": "Media storage", "ok": ok, "detail": f"object storage ({host}): {message} (permanent, private)"}
    return {"id": "storage", "label": "Media storage", "ok": not s.is_production,
            "detail": "local disk on the server. Generated images, videos and movies are lost on redeploy unless it is a persistent disk; set STORAGE_BACKEND=s3 for permanent storage"}


def collect() -> dict:
    s = get_settings()
    email = get_email_provider()
    db_kind = _db_kind(s.database_url)
    items = [
        {"id": "environment", "label": "Environment", "ok": s.is_production, "detail": f"APP_ENV={s.app_env}"
         + ("" if s.is_production else " (set APP_ENV=production on the live server)")},
        {"id": "auth_secret", "label": "Login signing secret", "ok": s.auth_secret_key != INSECURE_DEFAULT_SECRET and len(s.auth_secret_key) >= MIN_PRODUCTION_SECRET_LENGTH,
         "detail": "AUTH_SECRET_KEY is set and long enough" if s.auth_secret_key != INSECURE_DEFAULT_SECRET else "AUTH_SECRET_KEY is missing"},
        {"id": "frontend", "label": "Frontend address", "ok": not s.frontend_url.startswith("http://localhost"), "detail": f"FRONTEND_URL={s.frontend_url}"},
        {"id": "cors", "label": "Allowed browser origins", "ok": not any("localhost" in o or o == "*" for o in s.cors_origin_list) or not s.is_production,
         "detail": ", ".join(s.cors_origin_list)},
        {"id": "database", "label": "Database", "ok": db_kind != "sqlite" or not s.is_production,
         "detail": f"{db_kind}" + (": SQLite file on the server disk. On Render's free tier this is WIPED on every redeploy/restart (not permanent storage); set DATABASE_URL to PostgreSQL" if db_kind == "sqlite" else ": external database (permanent)")},
        _storage_item(s),
        {"id": "email", "label": "Email (sign-up codes, password reset)", "ok": email.is_configured(),
         "detail": f"provider={email.name}" + ("" if email.is_configured() else " is NOT configured: sign-up verification and password reset will say email isn't set up")
         + ("" if s.require_email_verification else "; email verification is switched OFF")},
        {"id": "google", "label": "Continue with Google", "ok": s.google_enabled, "detail": ("redirect URI " + s.google_redirect_uri) if s.google_enabled else "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI not all set"},
        {"id": "razorpay", "label": "Razorpay payments", "ok": bool(s.razorpay_key_id and s.razorpay_key_secret),
         "detail": f"mode: {razorpay_mode(s.razorpay_key_id)}" + ("" if s.razorpay_key_id and s.razorpay_key_secret else "; RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set")},
        {"id": "razorpay_webhook", "label": "Razorpay webhook secret", "ok": bool(s.razorpay_webhook_secret),
         "detail": "set" if s.razorpay_webhook_secret else "RAZORPAY_WEBHOOK_SECRET not set: webhooks are refused"},
        {"id": "video", "label": "fal.ai video", "ok": bool(s.video_provider_api_key or s.pollinations_video_api_key),
         "detail": "VIDEO_PROVIDER_API_KEY " + ("set" if s.video_provider_api_key else "not set" + (" (Pollinations is used instead)" if s.pollinations_video_api_key else ""))},
        {"id": "image", "label": "fal.ai image", "ok": bool(s.image_provider_api_key or s.video_provider_api_key or s.pollinations_image_api_key),
         "detail": "key set" + (" (shared with video)" if not s.image_provider_api_key and s.video_provider_api_key else "") if (s.image_provider_api_key or s.video_provider_api_key) else "IMAGE_PROVIDER_API_KEY / VIDEO_PROVIDER_API_KEY not set"},
        {"id": "pollinations_image", "label": "Pollinations image", "ok": bool(s.pollinations_image_api_key), "detail": "POLLINATIONS_IMAGE_API_KEY " + ("set" if s.pollinations_image_api_key else "not set")},
        {"id": "pollinations_video", "label": "Pollinations video", "ok": bool(s.pollinations_video_api_key), "detail": "POLLINATIONS_VIDEO_API_KEY " + ("set" if s.pollinations_video_api_key else "not set")},
        {"id": "knowlez", "label": "Knowlez voice (TTS)", "ok": bool(s.knowlez_api_key), "detail": "KNOWLEZ_API_KEY " + ("set" if s.knowlez_api_key else "not set")},
        {"id": "worker", "label": "Background worker", "ok": s.worker_enabled, "detail": f"WORKER_ENABLED={str(s.worker_enabled).lower()}" + ("" if s.worker_enabled else ": jobs only run if you start the worker separately")},
        {"id": "simulator", "label": "Development simulator", "ok": not s.enable_dev_simulator, "detail": "off" if not s.enable_dev_simulator else "ON (never use in production)"},
        {"id": "admins", "label": "Admin accounts", "ok": bool(s.admin_email_set), "detail": f"{len(s.admin_email_set)} admin email(s) configured"},
    ]
    return {"items": items, "all_ok": all(i["ok"] for i in items)}
