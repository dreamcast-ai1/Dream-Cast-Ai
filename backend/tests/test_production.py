import jwt
import pytest

from app.config import INSECURE_DEFAULT_SECRET, Settings, get_settings
from app.main import app
from app.security import auth_rate_limit

NETLIFY = "https://dreamcaastai.netlify.app"
LOCAL = "http://localhost:5173"


def preflight(client, origin, path="/api/projects", method="POST"):
    return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method,
                                         "Access-Control-Request-Headers": "authorization,content-type"})


# ------------------------------------------------------------------ health
def test_health_endpoints_need_no_authentication(client):
    for url in ("/health", "/api/health"):
        r = client.get(url)
        assert r.status_code == 200 and r.json() == {"status": "ok"}


# ------------------------------------------------------------------ CORS
@pytest.mark.parametrize("origin", [NETLIFY, LOCAL])
def test_cors_allows_the_production_and_local_frontends(client, origin):
    r = preflight(client, origin)
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in r.headers                      # never credentials + CORS
    plain = client.get("/health", headers={"Origin": origin})
    assert plain.headers["access-control-allow-origin"] == origin and plain.headers["access-control-allow-origin"] != "*"


@pytest.mark.parametrize("origin", ["https://evil.example.com", "http://dreamcaastai.netlify.app", "https://dreamcaastai.netlify.app.evil.com",
                                    "https://other.netlify.app"])
def test_cors_rejects_other_origins(client, origin):
    r = preflight(client, origin)
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": origin}).headers


def test_cors_config_is_exact_origins_only():
    assert Settings(cors_origins="*").cors_origin_list == [LOCAL, NETLIFY]              # a wildcard is ignored, never honoured
    assert Settings(cors_origins="").cors_origin_list == [LOCAL, NETLIFY]               # empty falls back to local dev
    s = Settings(cors_origins=f"{LOCAL}/, {NETLIFY}/ ,{LOCAL}")
    assert s.cors_origin_list == [LOCAL, NETLIFY]
    assert "*" not in get_settings().cors_origin_list


def test_unauthenticated_and_forbidden_responses_are_json_errors(client, make_user):
    r = client.get("/api/projects")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    h, _ = make_user()
    r = client.get("/api/admin/stats", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.get("/api/does-not-exist", headers=h).json()["error"]["message"]


# ------------------------------------------------------------------ configuration
def test_database_url_from_render_style_postgres_scheme_is_normalised():
    # Render/Heroku "postgres://" and a bare "postgresql://" both become the psycopg 3 driver form; an explicit driver is kept
    assert Settings(database_url="postgres://u:p@host/db").database_url == "postgresql+psycopg://u:p@host/db"
    assert Settings(database_url="postgresql://u:p@host/db").database_url == "postgresql+psycopg://u:p@host/db"
    assert Settings(database_url="postgresql+psycopg://u:p@host/db").database_url == "postgresql+psycopg://u:p@host/db"


def test_production_refuses_the_development_secret():
    with pytest.raises(ValueError):
        Settings(app_env="production", auth_secret_key=INSECURE_DEFAULT_SECRET)
    with pytest.raises(ValueError):
        Settings(app_env="production", auth_secret_key="")
    assert Settings(app_env="production", auth_secret_key="x" * 40).is_production


def test_api_docs_are_hidden_in_production():
    from app.main import create_app
    import app.main as main_module
    prod = Settings(app_env="production", auth_secret_key="x" * 40)
    main_module.get_settings = lambda: prod
    try:
        assert create_app().docs_url is None and create_app().openapi_url is None
    finally:
        main_module.get_settings = get_settings


# ------------------------------------------------------------------ rate limiting behind a proxy
def test_rate_limit_uses_the_real_client_ip_behind_a_proxy(client, monkeypatch):
    from app.security import login_failure_limit
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 2)
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    auth_rate_limit.reset()
    login_failure_limit.reset()
    login = lambda ip, email: client.post("/api/auth/login", json={"email": email, "password": "wrongwrong"}, headers={"X-Forwarded-For": f"{ip}, 10.0.0.1"})
    assert [login("1.1.1.1", f"a{i}@example.com").status_code for i in range(3)] == [401, 401, 429]     # three different accounts, one visitor
    assert login("2.2.2.2", "b@example.com").status_code == 401                                         # a different visitor is not locked out
    auth_rate_limit.reset()
    login_failure_limit.reset()


def test_forwarded_header_is_ignored_when_not_trusted(client, monkeypatch):
    from app.security import login_failure_limit
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 2)
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", False)
    auth_rate_limit.reset()
    login_failure_limit.reset()
    login = lambda ip, email: client.post("/api/auth/login", json={"email": email, "password": "wrongwrong"}, headers={"X-Forwarded-For": ip})
    assert [login(f"9.9.9.{i}", f"c{i}@example.com").status_code for i in range(3)] == [401, 401, 429]   # spoofing the header gains nothing
    auth_rate_limit.reset()
    login_failure_limit.reset()


def test_failed_logins_are_limited_per_account_even_if_the_ip_header_is_spoofed(client, make_user, monkeypatch):
    from app.security import login_failure_limit
    make_user("victim@example.com", "correct-password-1")
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 3)
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    auth_rate_limit.reset()
    login_failure_limit.reset()
    try:
        bad = lambda i, pw="wrong-password-1": client.post("/api/auth/login", json={"email": "victim@example.com", "password": pw}, headers={"X-Forwarded-For": f"7.7.7.{i}"})
        assert [bad(i).status_code for i in range(5)] == [401, 401, 401, 429, 429]       # a new fake IP each time gains nothing
        assert bad(9, "correct-password-1").status_code == 429                            # the account is paused for a minute
        assert client.post("/api/auth/login", json={"email": "other@example.com", "password": "x" * 10}).status_code == 401   # other accounts unaffected
    finally:
        auth_rate_limit.reset()
        login_failure_limit.reset()


# ------------------------------------------------------------------ authentication hardening
def test_bad_tokens_are_rejected(client, make_user):
    h, user = make_user()
    secret = get_settings().auth_secret_key
    good = jwt.encode({"sub": user["id"], "purpose": "access", "iss": "dreamcast", "exp": 4102444800}, secret, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {good}"}).status_code == 200
    forged = jwt.encode({"sub": user["id"], "purpose": "access", "iss": "dreamcast", "exp": 4102444800}, "another-secret-another-secret-123", algorithm="HS256")
    expired = jwt.encode({"sub": user["id"], "purpose": "access", "iss": "dreamcast", "exp": 1}, secret, algorithm="HS256")
    none_alg = jwt.encode({"sub": user["id"], "purpose": "access", "iss": "dreamcast"}, None, algorithm="none")
    wrong_purpose = jwt.encode({"sub": user["id"], "purpose": "reset", "iss": "dreamcast", "exp": 4102444800}, secret, algorithm="HS256")
    no_issuer = jwt.encode({"sub": user["id"], "purpose": "access", "exp": 4102444800}, secret, algorithm="HS256")
    for bad in (forged, expired, none_alg, wrong_purpose, no_issuer, "garbage", ""):
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401, bad[:20]
    assert client.get("/api/auth/me", headers={"Authorization": good}).status_code == 401           # missing "Bearer"


def test_role_changes_apply_immediately_without_logging_in_again(client, make_user):
    ah, admin = make_user("boss@example.com")
    h, user = make_user()
    assert client.get("/api/admin/stats", headers=h).status_code == 403
    assert client.patch(f"/api/admin/users/{user['id']}", headers=ah, json={"role": "ADMIN"}).status_code == 200
    assert client.get("/api/admin/stats", headers=h).status_code == 200            # same token, new role
    assert client.patch(f"/api/admin/users/{user['id']}", headers=ah, json={"role": "USER"}).status_code == 200
    assert client.get("/api/admin/stats", headers=h).status_code == 403


def test_register_login_session_flow_and_no_secret_leaks(client):
    r = client.post("/api/auth/register", json={"email": "New@Example.com", "password": "Passw0rd!x", "name": "New"})
    assert r.status_code == 200
    body = r.json()
    assert "password" not in str(body).lower() and "password_hash" not in str(body)
    token = body["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "new@example.com"
    assert client.post("/api/auth/login", json={"email": "new@example.com", "password": "Passw0rd!x"}).status_code == 200
    assert get_settings().auth_secret_key not in me.text and get_settings().auth_secret_key not in r.text
