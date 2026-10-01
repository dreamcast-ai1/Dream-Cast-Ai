"""Production-readiness: configuration rules, the public-route allow-list, and key hygiene for the real providers."""
import logging
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import app

from .helpers import FakeFal, generate, make_project, run_all, use_fal

REPO = Path(__file__).resolve().parents[2]
NETLIFY = "https://dreamcaastai.netlify.app"
GOOD_SECRET = "x" * 48


def example_values() -> dict:
    out = {}
    for line in (REPO / ".env.example").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def production(**kw) -> Settings:
    """Settings as the deployed backend sees them: the documented example values, nothing from this machine's environment."""
    return Settings(_env_file=None, **{**example_values(), "auth_secret_key": GOOD_SECRET, **kw})


# ------------------------------------------------------------------ configuration
def test_the_example_env_is_a_safe_production_starting_point():
    s = production()
    assert s.is_production and s.enable_dev_simulator is False and s.worker_enabled is True and s.storage_backend == "local"
    assert s.cors_origin_list == [NETLIFY] and s.frontend_url == NETLIFY
    assert s.database_url.endswith("backend/dreamcast.db")                       # blank DATABASE_URL = the default SQLite file
    assert not (s.razorpay_key_id or s.razorpay_key_secret or s.razorpay_webhook_secret or s.video_provider_api_key)   # placeholders are not credentials


def test_unfilled_placeholders_never_count_as_credentials():
    s = Settings(_env_file=None, app_env="development", razorpay_key_id="REPLACE_WITH_RAZORPAY_TEST_KEY_ID", razorpay_key_secret="REPLACE_WITH_X",
                 razorpay_webhook_secret="REPLACE_WITH_Y", video_provider_api_key="REPLACE_WITH_FAL_AI_KEY", llm_api_key="REPLACE_WITH_Z",
                 auth_secret_key="REPLACE_WITH_A_LONG_RANDOM_SECRET")
    assert (s.razorpay_key_id, s.razorpay_key_secret, s.razorpay_webhook_secret, s.video_provider_api_key, s.llm_api_key) == ("",) * 5
    from app.config import INSECURE_DEFAULT_SECRET
    assert s.auth_secret_key == INSECURE_DEFAULT_SECRET                         # fine for development only...
    with pytest.raises(ValidationError):
        production(auth_secret_key="REPLACE_WITH_A_LONG_RANDOM_SECRET")          # ...and refused in production


@pytest.mark.parametrize("secret", ["", "short-secret", "y" * 31])
def test_production_requires_a_strong_secret(secret):
    with pytest.raises(ValidationError):
        production(auth_secret_key=secret)
    assert production(auth_secret_key="y" * 32)                                   # 32+ characters is accepted


def test_production_cors_has_no_localhost_unless_configured_and_never_a_wildcard():
    assert production(cors_origins="").cors_origin_list == [NETLIFY]
    assert production(cors_origins="*").cors_origin_list == [NETLIFY]
    assert production(cors_origins="https://other.example.com/").cors_origin_list == ["https://other.example.com", NETLIFY]
    dev = Settings(_env_file=None, app_env="development", cors_origins=NETLIFY)
    assert dev.cors_origin_list == [NETLIFY, "http://localhost:5173"]            # local development keeps working


def test_production_never_points_links_at_localhost_and_never_runs_the_simulator():
    s = production(frontend_url="http://localhost:5173", enable_dev_simulator="true")
    assert s.frontend_url == NETLIFY and s.enable_dev_simulator is False
    assert production(worker_enabled="true").worker_enabled is True


# ------------------------------------------------------------------ every route is protected unless it is meant to be public
PUBLIC = {("GET", "/api/auth/config"), ("GET", "/api/health"), ("GET", "/api/subscription/plans"), ("POST", "/api/auth/register"),
          ("POST", "/api/auth/login"), ("POST", "/api/auth/forgot-password"), ("POST", "/api/auth/reset-password"),
          ("POST", "/api/auth/verify-email"), ("POST", "/api/auth/resend-otp"), ("GET", "/api/auth/google/start"), ("GET", "/api/auth/google/callback"),
          ("POST", "/api/payments/razorpay/webhook")}


def test_every_api_route_rejects_anonymous_callers_except_the_public_allow_list(client):
    unexpected, checked = [], 0
    for path, ops in app.openapi()["paths"].items():
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in ops:
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            checked += 1
            r = client.request(method.upper(), url, json={} if method != "get" else None)
            if (method.upper(), path) in PUBLIC:
                if path == "/api/payments/razorpay/webhook":
                    assert r.status_code == 400                                     # unsigned webhook is refused
                continue
            if r.status_code != 401:
                unexpected.append((method.upper(), path, r.status_code))
    assert checked > 60 and unexpected == []


def test_admin_routes_all_require_the_admin_role(client, make_user):
    h, _ = make_user()
    admin_paths = [(m.upper(), re.sub(r"\{[^}]+\}", "x", p)) for p, ops in app.openapi()["paths"].items() if p.startswith("/api/admin") for m in ops]
    assert len(admin_paths) >= 8
    for method, url in admin_paths:
        assert client.request(method, url, headers=h, json={}).status_code == 403, (method, url)


# ------------------------------------------------------------------ provider keys stay on the server
def test_video_provider_key_is_never_logged_or_returned(client, make_user, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, "video", prompt="A knight rides at dawn.", options={"duration_seconds": 10, "aspect_ratio": "16:9"}, project_id=pid)
    assert r.status_code == 201
    run_all()
    bodies = [client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).text, client.get("/api/generate/schema", headers=h).text,
              client.get(f"/api/projects/{pid}/assets", headers=h).text, client.get("/api/subscription/current", headers=h).text]
    assert "test-video-key" not in "\n".join(bodies) + caplog.text


def test_frontend_source_and_build_inputs_hold_no_provider_variables():
    for f in list((REPO / "frontend/src").rglob("*.ts*")) + [REPO / "frontend/.env.example", REPO / "frontend/index.html"]:
        text = f.read_text()
        assert not re.search(r"VITE_[A-Z_]*(SECRET|API_KEY|RAZORPAY|PROVIDER)", text), f.name
        assert "VIDEO_PROVIDER_API_KEY" not in text and "razorpay_key_secret" not in text.lower(), f.name


def test_a_failed_configuration_never_echoes_secrets(monkeypatch):
    """pydantic normally prints the whole input dictionary in a validation error, which would put secrets into the startup log."""
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, app_env="production", auth_secret_key="too-short-SECRETVALUE", razorpay_key_secret="rzp-SECRETVALUE-2",
                 s3_secret_access_key="s3-SECRETVALUE-3", smtp_password="smtp-SECRETVALUE-4")
    assert "AUTH_SECRET_KEY must be at least 32" in str(e.value)
    assert "SECRETVALUE" not in str(e.value) and "input_value" not in str(e.value)


def test_production_without_cors_origins_allows_only_the_live_frontend():
    s = Settings(_env_file=None, app_env="production", auth_secret_key=GOOD_SECRET)           # CORS_ORIGINS not set at all
    assert s.cors_origin_list == [NETLIFY]
    assert Settings(_env_file=None, app_env="development").cors_origin_list == ["http://localhost:5173", NETLIFY]
