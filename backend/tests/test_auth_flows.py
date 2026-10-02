"""Email OTP verification, email password reset and Google sign-in. Email is a mock mailbox and Google is a mock whose ID tokens are signed
with a test RSA key, so the real validation code (signature, issuer, audience, expiry, nonce, verified email) runs. No real external service."""
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.email import EmailError, EmailProvider, set_email_provider
from app.email.smtp import SmtpEmailProvider
from app.models import EmailVerification, PasswordResetToken, Subscription, User
from app.security import login_failure_limit, otp_send_limit, otp_verify_limit, reset_send_limit
from app.services import google_oauth
from app.services.google_oauth import GoogleClient, set_google_client

BACKEND = Path(__file__).resolve().parents[1]
CLIENT_ID, CLIENT_SECRET = "test-client-id.apps.googleusercontent.com", "GOCSPX-test-google-secret-value"
PW = "correct-horse-1"


# ------------------------------------------------------------------ mocks
class Mailbox(EmailProvider):
    name = "mailbox"

    def __init__(self, configured=True, fail=False):
        self.sent: list[tuple[str, str, str]] = []
        self.configured, self.fail = configured, fail

    def is_configured(self):
        return self.configured

    def send(self, to, subject, text):
        if self.fail:
            raise EmailError("boom")
        self.sent.append((to, subject, text))

    def to(self, email):
        return [m for m in self.sent if m[0] == email]

    def code(self, email) -> str:
        return re.search(r"code is (\d{6})\.", self.to(email)[-1][2]).group(1)

    def token(self, email) -> str:
        return re.search(r"token=([\w-]+)", self.to(email)[-1][2]).group(1)


@pytest.fixture
def mail(monkeypatch):
    box = Mailbox()
    set_email_provider(box)
    monkeypatch.setattr(get_settings(), "require_email_verification", True)
    for limiter in (otp_send_limit, otp_verify_limit, reset_send_limit, login_failure_limit):
        limiter.reset()
    yield box
    set_email_provider(None)
    for limiter in (otp_send_limit, otp_verify_limit, reset_send_limit, login_failure_limit):
        limiter.reset()


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeGoogle(GoogleClient):
    """exchange_code is the only 'network' call; the token it returns is what the tests vary. Signature keys come from the test key pair."""

    def __init__(self):
        self.nonce = ""
        self.claims: dict = {}
        self.sign_key = KEY
        self.codes_used: list[str] = []

    def exchange_code(self, code):
        if code != "good-code":
            raise google_oauth.GoogleAuthError("token endpoint answered HTTP 400")
        self.codes_used.append(code)
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "g-1001", "email": "gina@example.com", "email_verified": True,
                  "name": "Gina Google", "picture": "https://example.com/p.png", "iat": now, "exp": now + 600, "nonce": self.nonce, **self.claims}
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.sign_key, algorithm="RS256")

    def signing_key(self, id_token):
        return KEY.public_key()


@pytest.fixture
def google(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(s, "google_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(s, "google_redirect_uri", "http://testserver/api/auth/google/callback")
    monkeypatch.setattr(s, "frontend_url", "http://frontend.test")
    fake = FakeGoogle()
    set_google_client(fake)
    yield fake
    set_google_client(None)


def start_google(client, fake):
    r = client.get("/api/auth/google/start", follow_redirects=False)
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    fake.nonce = q["nonce"][0]
    return r, q


def finish_google(client, state, code="good-code"):
    return client.get("/api/auth/google/callback", params={"code": code, "state": state}, follow_redirects=False)


def google_login(client, fake, **claims):
    fake.claims = claims
    _, q = start_google(client, fake)
    return finish_google(client, q["state"][0])


def register(client, email="new@example.com", password=PW, name="New User"):
    return client.post("/api/auth/register", json={"email": email, "password": password, "name": name})


def login(client, email="new@example.com", password=PW):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def verify(client, email, code):
    return client.post("/api/auth/verify-email", json={"email": email, "code": code})


def db_user(email):
    with SessionLocal() as db:
        return db.query(User).filter_by(email=email).first()


def fragment_token(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).fragment)["access_token"][0]


# ------------------------------------------------------------------ registration
def test_register_creates_an_unverified_account_and_emails_a_code_without_a_token(client, mail):
    r = register(client)
    assert r.status_code == 200 and r.json()["verification_required"] is True and r.json()["email"] == "new@example.com"
    assert "access_token" not in r.json() and r.json()["expires_in"] == 600
    user = db_user("new@example.com")
    assert user.email_verified is False and user.password_hash and user.password_hash != PW and user.auth_provider == "local"
    with SessionLocal() as db:
        assert db.query(Subscription).filter_by(user_id=user.id).one().plan_id == "teaser"
    [(to, subject, body)] = mail.to("new@example.com")
    assert re.search(r"code is \d{6}\.", body) and "10 minutes" in body and subject


def test_unverified_accounts_cannot_log_in_until_the_code_is_entered(client, mail):
    register(client)
    r = login(client)
    assert r.status_code == 403 and r.json()["error"]["code"] == "email_not_verified" and "verify your email" in r.json()["error"]["message"].lower()
    assert login(client, password="wrong-password-9").status_code == 401           # wrong password still says nothing about verification
    v = verify(client, "new@example.com", mail.code("new@example.com"))
    assert v.status_code == 200 and v.json()["user"]["email_verified"] is True
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + v.json()["access_token"]}).status_code == 200
    assert login(client).status_code == 200


def test_registration_validation(client, mail):
    assert register(client, email="not-an-email").status_code == 422
    assert register(client, password="short").status_code == 422
    assert register(client, password="x" * 200).status_code == 422
    assert mail.sent == [] and db_user("new@example.com") is None


def test_duplicate_registration_does_not_reveal_the_account(client, mail):
    register(client)
    verify(client, "new@example.com", mail.code("new@example.com"))
    mail.sent.clear()
    r = register(client, password="attacker-password-1")
    assert r.status_code == 200 and set(r.json()) == {"verification_required", "email", "expires_in", "resend_after"}     # same shape as a fresh sign-up
    assert "already has one" in mail.to("new@example.com")[0][2]                    # the real owner is told by email
    assert login(client).status_code == 200 and login(client, password="attacker-password-1").status_code == 401        # password unchanged
    assert verify(client, "new@example.com", "123456").status_code == 400


def test_signing_up_again_before_verifying_replaces_the_pending_password(client, mail):
    register(client, password="attackers-password-1")
    register(client, password="owners-password-2")                                  # whoever controls the inbox wins
    verify(client, "new@example.com", mail.code("new@example.com"))
    assert login(client, password="owners-password-2").status_code == 200
    assert login(client, password="attackers-password-1").status_code == 401


def test_registration_needs_a_working_email_service(client, mail):
    set_email_provider(Mailbox(configured=False))
    r = register(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "email_not_configured" and db_user("new@example.com") is None
    set_email_provider(Mailbox(fail=True))
    r = register(client)
    assert r.status_code == 502 and r.json()["error"]["code"] == "email_send_failed" and "Traceback" not in r.text
    assert db_user("new@example.com").email_verified is False                        # can retry with "resend"


def test_verification_can_be_switched_off_for_deployments_without_email(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "require_email_verification", False)
    r = register(client)
    assert r.status_code == 200 and r.json()["access_token"] and r.json()["user"]["email_verified"] is True
    assert register(client).status_code == 409


# ------------------------------------------------------------------ one-time codes
def test_wrong_code_fails_and_too_many_wrong_codes_lock_the_code(client, mail, monkeypatch):
    register(client)
    good = mail.code("new@example.com")
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(get_settings().otp_max_attempts):
        r = verify(client, "new@example.com", wrong)
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"
    assert verify(client, "new@example.com", good).status_code == 400                # locked: even the right code no longer works
    assert db_user("new@example.com").email_verified is False
    client.post("/api/auth/resend-otp", json={"email": "new@example.com"})           # a fresh code works again
    assert verify(client, "new@example.com", mail.code("new@example.com")).status_code == 200


def test_expired_code_fails(client, mail):
    register(client)
    with SessionLocal() as db:
        row = db.query(EmailVerification).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert verify(client, "new@example.com", mail.code("new@example.com")).status_code == 400
    assert db_user("new@example.com").email_verified is False


def test_a_code_works_once(client, mail):
    register(client)
    code = mail.code("new@example.com")
    assert verify(client, "new@example.com", code).status_code == 200
    assert verify(client, "new@example.com", code).status_code == 400


def test_resend_cancels_the_previous_code(client, mail):
    register(client)
    first = mail.code("new@example.com")
    r = client.post("/api/auth/resend-otp", json={"email": "new@example.com"})
    assert r.status_code == 200 and len(mail.to("new@example.com")) == 2
    second = mail.code("new@example.com")
    if first != second:
        assert verify(client, "new@example.com", first).status_code == 400            # the old code is dead
    assert verify(client, "new@example.com", second).status_code == 200


def test_resend_never_reveals_whether_an_email_has_an_account(client, mail):
    register(client)
    mail.sent.clear()
    known = client.post("/api/auth/resend-otp", json={"email": "new@example.com"})
    unknown = client.post("/api/auth/resend-otp", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    assert len(mail.sent) == 1 and mail.sent[0][0] == "new@example.com"
    verify(client, "new@example.com", mail.code("new@example.com"))
    assert client.post("/api/auth/resend-otp", json={"email": "new@example.com"}).json() == known.json()     # verified accounts: same answer, no email
    assert len(mail.sent) == 1
    assert verify(client, "nobody@example.com", "123456").json() == verify(client, "new@example.com", "123456").json()


def test_resend_has_a_cooldown_and_an_hourly_limit(client, mail, monkeypatch):
    monkeypatch.setattr(get_settings(), "otp_resend_seconds", 60)
    register(client)
    client.post("/api/auth/resend-otp", json={"email": "new@example.com"})
    assert len(mail.to("new@example.com")) == 1                                       # too soon: no second email
    monkeypatch.setattr(get_settings(), "otp_resend_seconds", 0)
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 100)
    otp_send_limit.reset()
    for _ in range(8):
        client.post("/api/auth/resend-otp", json={"email": "new@example.com"})
    assert len(mail.to("new@example.com")) <= 1 + 5                                    # never more than 5 an hour


def test_code_guessing_is_rate_limited(client, mail, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 100)
    otp_verify_limit.reset()
    register(client)
    codes = [verify(client, "new@example.com", f"{i:06d}").status_code for i in range(12)]
    assert 429 in codes and codes.index(429) == 10                                     # 10 guesses, then blocked
    assert verify(client, "new@example.com", mail.code("new@example.com")).status_code == 429


def test_codes_never_leak_into_responses_logs_or_the_database(client, mail, caplog):
    caplog.set_level(logging.DEBUG)
    responses = [register(client).text]
    code = mail.code("new@example.com")
    responses += [client.post("/api/auth/resend-otp", json={"email": "new@example.com"}).text, verify(client, "new@example.com", "000001").text,
                  login(client).text]
    code = mail.code("new@example.com")
    responses.append(verify(client, "new@example.com", code).text)
    assert not any(code in r for r in responses[:-1]) and code not in caplog.text
    with SessionLocal() as db:
        for row in db.query(EmailVerification).all():
            assert code not in row.code_hash and len(row.code_hash) == 64 and row.code_hash != code


# ------------------------------------------------------------------ password reset
def test_password_reset_by_email(client, mail):
    register(client)
    verify(client, "new@example.com", mail.code("new@example.com"))
    r = client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    assert r.status_code == 200 and "token" not in r.text
    token = mail.token("new@example.com")
    assert login(client).status_code == 200                                           # asking for a reset changes nothing yet
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "brand-new-pass-1"}).status_code == 200
    assert login(client, password="brand-new-pass-1").status_code == 200 and login(client).status_code == 401


def test_reset_token_expires_and_is_single_use(client, mail):
    register(client)
    verify(client, "new@example.com", mail.code("new@example.com"))
    client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    token = mail.token("new@example.com")
    with SessionLocal() as db:
        row = db.query(PasswordResetToken).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "brand-new-pass-1"}).status_code == 400
    assert login(client).status_code == 200                                           # a failed reset never touches the password
    client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    fresh = mail.token("new@example.com")
    assert client.post("/api/auth/reset-password", json={"token": fresh, "password": "brand-new-pass-1"}).status_code == 200
    r = client.post("/api/auth/reset-password", json={"token": fresh, "password": "yet-another-pass-1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_reset_token"


def test_a_new_reset_request_cancels_the_earlier_link(client, mail):
    register(client)
    verify(client, "new@example.com", mail.code("new@example.com"))
    client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    first = mail.token("new@example.com")
    client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    assert client.post("/api/auth/reset-password", json={"token": first, "password": "brand-new-pass-1"}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": mail.token("new@example.com"), "password": "brand-new-pass-1"}).status_code == 200


def test_forgot_password_gives_the_same_answer_for_unknown_emails(client, mail):
    register(client)
    verify(client, "new@example.com", mail.code("new@example.com"))
    mail.sent.clear()
    known = client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    assert [m[0] for m in mail.sent] == ["new@example.com"]


def test_forgot_password_needs_email_and_never_logs_or_returns_the_token(client, mail, caplog):
    caplog.set_level(logging.DEBUG)
    register(client)
    verify(client, "new@example.com", mail.code("new@example.com"))
    r = client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    token = mail.token("new@example.com")
    assert token not in r.text and token not in caplog.text and "reset-password" not in caplog.text
    with SessionLocal() as db:
        row = db.query(PasswordResetToken).one()
        assert row.token_hash != token and token not in row.token_hash and len(row.token_hash) == 64
    set_email_provider(Mailbox(configured=False))
    r = client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "email_not_configured"


def test_reset_email_link_points_at_the_frontend(client, mail, monkeypatch):
    monkeypatch.setattr(get_settings(), "frontend_url", "https://app.example.com/")
    register(client)
    verify(client, "new@example.com", mail.code("new@example.com"))
    client.post("/api/auth/forgot-password", json={"email": "new@example.com"})
    assert "https://app.example.com/reset-password?token=" in mail.to("new@example.com")[-1][2]


# ------------------------------------------------------------------ Google
def test_google_start_redirects_to_google_with_state_and_a_bound_cookie(client, google):
    r, q = start_google(client, google)
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == [CLIENT_ID] and q["response_type"] == ["code"] and q["scope"] == ["openid email profile"]
    assert q["redirect_uri"] == ["http://testserver/api/auth/google/callback"] and q["state"][0] and q["nonce"][0]
    assert CLIENT_SECRET not in r.headers["location"]
    cookie = r.headers["set-cookie"]
    assert "dc_oauth_state=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_google_is_unavailable_without_configuration(client):
    assert client.get("/api/auth/google/start", follow_redirects=False).status_code == 503
    assert client.get("/api/auth/config").json()["google_enabled"] is False


def test_google_config_flag_never_exposes_secrets(client, google):
    cfg = client.get("/api/auth/config")
    assert cfg.json()["google_enabled"] is True and CLIENT_SECRET not in cfg.text and CLIENT_ID not in cfg.text


def test_new_google_user_is_created_verified_and_signed_in(client, google):
    r = google_login(client, google)
    assert r.status_code == 302 and r.headers["location"].startswith("http://frontend.test/auth/callback#access_token=")
    token = fragment_token(r)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["email"] == "gina@example.com" and me["name"] == "Gina Google" and me["auth_provider"] == "google" and me["email_verified"] is True
    user = db_user("gina@example.com")
    assert user.google_subject_id == "g-1001" and user.password_hash is None and user.avatar_url == "https://example.com/p.png"
    assert client.get("/api/subscription/current", headers={"Authorization": f"Bearer {token}"}).json()["plan"]["id"] == "teaser"
    assert login(client, "gina@example.com", PW).status_code == 401                  # no password exists for this account


def test_returning_google_user_gets_the_same_account(client, google):
    google_login(client, google)
    first = db_user("gina@example.com").id
    google_login(client, google, email="gina.renamed@example.com")                    # same Google account, changed address
    with SessionLocal() as db:
        assert db.query(User).count() == 1 and db.query(User).one().id == first


def test_google_links_to_a_verified_local_account_and_keeps_its_password(client, google, mail):
    register(client, "gina@example.com")
    verify(client, "gina@example.com", mail.code("gina@example.com"))
    google_login(client, google)
    user = db_user("gina@example.com")
    assert user.google_subject_id == "g-1001" and user.password_hash and user.email_verified
    assert login(client, "gina@example.com").status_code == 200
    with SessionLocal() as db:
        assert db.query(User).count() == 1


def test_google_linking_removes_a_password_set_by_someone_who_never_verified_the_email(client, google, mail):
    register(client, "gina@example.com", password="attackers-password-1")             # attacker pre-registers the victim's email
    google_login(client, google)                                                       # the real owner signs in with Google
    user = db_user("gina@example.com")
    assert user.email_verified and user.google_subject_id == "g-1001" and user.password_hash is None
    assert login(client, "gina@example.com", "attackers-password-1").status_code == 401


def test_google_refuses_a_second_google_identity_for_the_same_email(client, google):
    google_login(client, google)
    r = google_login(client, google, sub="g-2002")
    assert "error=google_failed" in r.headers["location"]
    assert db_user("gina@example.com").google_subject_id == "g-1001"


@pytest.mark.parametrize("claims,label", [
    ({"aud": "someone-elses-client-id"}, "wrong audience"),
    ({"iss": "https://evil.example.com"}, "wrong issuer"),
    ({"exp": 1000, "iat": 900}, "expired"),
    ({"nonce": "not-the-nonce"}, "nonce mismatch"),
    ({"email_verified": False}, "unverified email"),
    ({"email": None}, "no email"),
])
def test_invalid_google_id_tokens_are_refused_and_create_nothing(client, google, claims, label):
    r = google_login(client, google, **claims)
    assert r.status_code == 302 and r.headers["location"] == "http://frontend.test/login?error=google_failed", label
    assert "access_token" not in r.headers["location"] and db_user("gina@example.com") is None


def test_id_token_signed_with_the_wrong_key_is_refused(client, google):
    google.sign_key = OTHER_KEY
    r = google_login(client, google)
    assert "error=google_failed" in r.headers["location"] and db_user("gina@example.com") is None


def test_invalid_state_is_refused(client, google):
    _, q = start_google(client, google)
    good = q["state"][0]
    for bad in ("garbage", good[:-3] + "abc", ""):
        r = finish_google(client, bad)
        assert "error=google_failed" in r.headers["location"] and "access_token" not in r.headers["location"]
    assert db_user("gina@example.com") is None
    client.cookies.clear()                                                             # state from another browser: cookie missing
    assert "error=google_failed" in finish_google(client, good).headers["location"]
    other_start = client.get("/api/auth/google/start", follow_redirects=False)         # cookie belongs to a different attempt
    assert "error=google_failed" in finish_google(client, good).headers["location"] and other_start.status_code == 302
    expired = jwt.encode({"sub": "n", "purpose": "oauth_state", "iss": "dreamcast", "n": "n", "exp": 1000}, get_settings().auth_secret_key, algorithm="HS256")
    assert "error=google_failed" in finish_google(client, expired).headers["location"]


def test_state_cannot_be_an_access_token(client, google, make_user):
    h, _ = make_user()
    start_google(client, google)
    assert "error=google_failed" in finish_google(client, h["Authorization"].split()[1]).headers["location"]


def test_google_cancelled_or_rejected_code_returns_to_login(client, google):
    start_google(client, google)
    r = client.get("/api/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"] == "http://frontend.test/login?error=google_cancelled"
    _, q = start_google(client, google)
    assert "error=google_failed" in finish_google(client, q["state"][0], code="bad-code").headers["location"]


def test_google_cannot_sign_in_a_disabled_account(client, google):
    google_login(client, google)
    with SessionLocal() as db:
        db.query(User).one().is_active = False
        db.commit()
    assert "error=google_failed" in google_login(client, google).headers["location"]


def test_google_admin_email_gets_the_admin_role(client, google, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "gina@example.com")
    google_login(client, google)
    assert db_user("gina@example.com").role == "ADMIN"


def test_google_secrets_never_reach_responses_or_logs(client, google, caplog):
    caplog.set_level(logging.DEBUG)
    r1, q = start_google(client, google)
    r2 = finish_google(client, q["state"][0])
    r3 = finish_google(client, "garbage", code="good-code")
    google_login(client, google, aud="other")
    app_logs = "\n".join(rec.getMessage() for rec in caplog.records if not rec.name.startswith(("httpx", "asyncio")))    # the test client logs its own URLs
    blob = "".join(r.text + str(dict(r.headers)) for r in (r1, r2, r3)) + app_logs
    assert CLIENT_SECRET not in blob and "good-code" not in app_logs and fragment_token(r2) not in app_logs


# ------------------------------------------------------------------ SMTP adapter
def test_smtp_adapter_sends_with_starttls_and_login_without_logging_secrets(monkeypatch, caplog):
    import smtplib
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, **kw): calls.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def has_extn(self, name): return name == "starttls"
        def starttls(self, context=None): calls.append(("starttls",))
        def login(self, u, p): calls.append(("login", u))
        def send_message(self, msg): calls.append(("send", msg["To"], msg["Subject"], msg["From"]))
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    s = get_settings()
    for k, v in dict(smtp_host="smtp.test", smtp_port=587, smtp_username="mailer", smtp_password="smtp-pass-123", smtp_from_email="no-reply@example.com").items():
        monkeypatch.setattr(s, k, v)
    caplog.set_level(logging.DEBUG)
    SmtpEmailProvider().send("a@example.com", "Hi", "Body")
    assert calls == [("connect", "smtp.test", 587), ("starttls",), ("login", "mailer"), ("send", "a@example.com", "Hi", "Dream Cast AI <no-reply@example.com>")]
    assert "smtp-pass-123" not in caplog.text

    class Broken(FakeSMTP):
        def send_message(self, msg): raise smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"secret detail smtp-pass-123")})
    monkeypatch.setattr(smtplib, "SMTP", Broken)
    with pytest.raises(EmailError):
        SmtpEmailProvider().send("a@example.com", "Hi", "Body")
    assert "smtp-pass-123" not in caplog.text and "secret detail" not in caplog.text


def test_smtp_adapter_is_not_configured_without_settings():
    assert not SmtpEmailProvider().is_configured()
    with pytest.raises(EmailError):
        SmtpEmailProvider().send("a@example.com", "Hi", "Body")


def test_placeholders_do_not_configure_email_or_google():
    s = Settings(_env_file=None, smtp_host="REPLACE_WITH_SMTP_HOST", smtp_username="REPLACE_WITH_SMTP_USERNAME", smtp_password="REPLACE_WITH_SMTP_PASSWORD",
                 smtp_from_email="REPLACE_WITH_FROM_EMAIL", google_client_id="REPLACE_WITH_GOOGLE_CLIENT_ID", google_client_secret="REPLACE_WITH_GOOGLE_CLIENT_SECRET",
                 google_redirect_uri="https://dreamcast-ai-backend.onrender.com/api/auth/google/callback")
    assert (s.smtp_host, s.smtp_username, s.smtp_password, s.smtp_from_email, s.google_client_id, s.google_client_secret) == ("",) * 6
    assert s.google_enabled is False
    assert Settings(_env_file=None, google_client_id="a", google_client_secret="b", google_redirect_uri="https://x/cb").google_enabled is True


# ------------------------------------------------------------------ migration: existing accounts stay usable
def test_migration_marks_existing_accounts_verified(tmp_path):
    db_file = tmp_path / "old.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}", "APP_ENV": "development"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "alembic", *a], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert run("upgrade", "0006").returncode == 0
    con = sqlite3.connect(db_file)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(2):
        con.execute("INSERT INTO users (id, email, name, auth_provider, role, is_active, created_at) VALUES (?, ?, 'N', 'local', 'USER', 1, ?)", (f"u{i}", f"u{i}@example.com", now))
    con.commit()
    con.close()
    r = run("upgrade", "head")
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT count(*), sum(email_verified) FROM users").fetchone() == (2, 2)
    assert {t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"email_verifications", "password_reset_tokens"}
    assert run("downgrade", "0006").returncode == 0 and run("upgrade", "head").returncode == 0
    assert "No new upgrade operations" in (run("check").stdout + run("check").stderr)


# ------------------------------------------------------------------ real-service readiness helpers
def test_brevo_adapter_sends_over_https_without_leaking_the_key(monkeypatch, caplog):
    import httpx
    from app.email.brevo import BrevoEmailProvider
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return httpx.Response(201, json={"messageId": "x"})
    monkeypatch.setattr(httpx, "post", fake_post)
    s = get_settings()
    for k, v in dict(brevo_api_key="brevo-secret-key-123", smtp_from_email="no-reply@example.com", smtp_from_name="Dream Cast AI").items():
        monkeypatch.setattr(s, k, v)
    caplog.set_level(logging.DEBUG)
    BrevoEmailProvider().send("a@example.com", "Hi", "Body 123456")
    assert seen["url"] == "https://api.brevo.com/v3/smtp/email" and seen["headers"]["api-key"] == "brevo-secret-key-123"
    assert seen["json"] == {"sender": {"name": "Dream Cast AI", "email": "no-reply@example.com"}, "to": [{"email": "a@example.com"}], "subject": "Hi",
                            "htmlContent": "<html><body><p>Body 123456</p></body></html>", "textContent": "Body 123456"}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, json={"message": "Key not found brevo-secret-key-123"}))
    with pytest.raises(EmailError):
        BrevoEmailProvider().send("a@example.com", "Hi", "Body")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("t")))
    with pytest.raises(EmailError):
        BrevoEmailProvider().send("a@example.com", "Hi", "Body")
    assert "brevo-secret-key-123" not in caplog.text and "Key not found" not in caplog.text


def test_email_provider_is_chosen_by_setting_and_placeholders_do_not_configure_brevo(monkeypatch):
    from app.email import registry
    from app.email.brevo import BrevoEmailProvider
    assert Settings(_env_file=None, brevo_api_key="REPLACE_WITH_BREVO_API_KEY").brevo_api_key == ""
    registry.set_email_provider(None)
    monkeypatch.setattr(get_settings(), "email_provider", "brevo")
    try:
        assert isinstance(registry.get_email_provider(), BrevoEmailProvider) and not registry.get_email_provider().is_configured()
        registry.set_email_provider(None)
        monkeypatch.setattr(get_settings(), "email_provider", "smtp")
        assert isinstance(registry.get_email_provider(), SmtpEmailProvider)
    finally:
        registry.set_email_provider(None)


def test_google_accepts_a_few_seconds_of_clock_difference(client, google):
    google.claims = {"iat": int(time.time()) + 8, "exp": int(time.time()) + 900}          # Google's clock slightly ahead of ours
    _, q = start_google(client, google)
    assert "access_token" in finish_google(client, q["state"][0]).headers["location"]


def test_admin_system_status_is_admin_only_and_never_returns_secrets(client, make_user, monkeypatch):
    s = get_settings()
    secrets_ = {"razorpay_key_secret": "rzp-secret-AAA111", "razorpay_webhook_secret": "whsec-BBB222", "google_client_secret": "GOCSPX-CCC333",
                "smtp_password": "smtp-pass-DDD444", "brevo_api_key": "brevo-EEE555", "video_provider_api_key": "fal-key-FFF666", "image_provider_api_key": "fal-key-GGG777"}
    for k, v in secrets_.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_PUBLICID123")
    ah, _ = make_user("boss@example.com")
    uh, _ = make_user()
    assert client.get("/api/admin/system").status_code == 401 and client.get("/api/admin/system", headers=uh).status_code == 403
    r = client.get("/api/admin/system", headers=ah)
    assert r.status_code == 200
    blob = r.text
    assert not [v for v in secrets_.values() if v in blob] and s.auth_secret_key not in blob and "PUBLICID123" not in blob      # not even the public key id
    items = {i["id"]: i for i in r.json()["items"]}
    assert items["razorpay"]["ok"] and "mode: test" in items["razorpay"]["detail"] and items["razorpay_webhook"]["ok"]
    assert items["video"]["ok"] and items["image"]["ok"] and items["database"]["detail"].startswith(s.database_url.split(":")[0].split("+")[0])
    assert items["simulator"]["ok"] is False                                                          # the test environment runs the simulator, and the page says so


def test_system_status_flags_unconfigured_services(client, make_user):
    ah, _ = make_user("boss@example.com")
    items = {i["id"]: i for i in client.get("/api/admin/system", headers=ah).json()["items"]}
    assert items["email"]["ok"] is False and items["google"]["ok"] is False and items["razorpay"]["ok"] is False and items["video"]["ok"] is False
    assert "NOT configured" in items["email"]["detail"]
