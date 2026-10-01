"""Google sign-in (OAuth 2.0 authorization-code flow with OpenID Connect), done entirely on the server.
The browser is only redirected to Google and back; the client secret and Google's tokens never reach it.

Safety checks: signed single-use `state` bound to the browser by a cookie (blocks login CSRF), a `nonce` echoed in the ID token (blocks replay),
and full ID-token validation (Google's signature, issuer, audience, expiry, nonce, verified email) before any account is touched."""
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AppError
from ..models import User
from ..security import create_token, decode_local_token
from . import subscriptions

log = logging.getLogger("dreamcast.google")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_COOKIE = "dc_oauth_state"
STATE_MINUTES = 10


class GoogleAuthError(Exception):
    """Sign-in failed for a reason the user can't fix by retrying with the same data. Details stay in the log (never tokens/codes)."""


class GoogleClient:
    """The two network operations, kept separate so tests can replace them while the validation below still runs for real."""

    def exchange_code(self, code: str) -> str:
        s = get_settings()
        try:
            r = httpx.post(TOKEN_URL, data={"code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
                                            "redirect_uri": s.google_redirect_uri, "grant_type": "authorization_code"}, timeout=15)
        except httpx.HTTPError as e:
            raise GoogleAuthError(f"token request failed: {type(e).__name__}")
        if r.status_code != 200 or not r.json().get("id_token"):
            raise GoogleAuthError(f"token endpoint answered HTTP {r.status_code}")
        return r.json()["id_token"]

    def signing_key(self, id_token: str):
        try:
            return jwt.PyJWKClient(JWKS_URL, cache_keys=True).get_signing_key_from_jwt(id_token).key
        except (jwt.PyJWTError, OSError) as e:
            raise GoogleAuthError(f"could not load Google's signing keys: {type(e).__name__}")


_client: GoogleClient | None = None


def get_google_client() -> GoogleClient:
    global _client
    if _client is None:
        _client = GoogleClient()
    return _client


def set_google_client(client: GoogleClient | None) -> None:
    global _client
    _client = client


def require_enabled() -> None:
    if not get_settings().google_enabled:
        raise AppError("Google sign-in isn't set up on this server yet.", 503, "google_not_configured")


def start() -> tuple[str, str]:
    """Returns (Google authorization URL, cookie value). The cookie holds the nonce that the state must match."""
    s = get_settings()
    nonce = secrets.token_urlsafe(24)
    state = create_token(nonce, "oauth_state", minutes=STATE_MINUTES, extra={"n": nonce})
    q = urlencode({"client_id": s.google_client_id, "redirect_uri": s.google_redirect_uri, "response_type": "code", "scope": "openid email profile",
                   "state": state, "nonce": nonce, "prompt": "select_account", "access_type": "online"})
    return f"{AUTH_URL}?{q}", nonce


def validate_id_token(id_token: str, nonce: str) -> dict:
    s = get_settings()
    try:
        claims = jwt.decode(id_token, get_google_client().signing_key(id_token), algorithms=["RS256"], audience=s.google_client_id,
                            leeway=15, options={"require": ["exp", "iat", "iss", "aud", "sub"]})      # leeway: a few seconds of clock difference with Google
    except jwt.PyJWTError as e:
        raise GoogleAuthError(f"id token rejected: {type(e).__name__}")
    if claims.get("iss") not in ISSUERS:
        raise GoogleAuthError("wrong issuer")
    if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise GoogleAuthError("nonce mismatch")
    if claims.get("email_verified") is not True or not claims.get("email"):
        raise GoogleAuthError("email not verified by Google")
    return claims


def complete(db: Session, code: str, state: str, cookie_nonce: str | None) -> User:
    """Validates everything, then finds, links or creates the local account. Raises GoogleAuthError on any failure."""
    require_enabled()
    try:
        st = decode_local_token(state, "oauth_state")
    except jwt.PyJWTError:
        raise GoogleAuthError("invalid or expired state")
    if not cookie_nonce or not secrets.compare_digest(st.get("n", ""), cookie_nonce):
        raise GoogleAuthError("state does not match this browser")
    claims = validate_id_token(get_google_client().exchange_code(code), st["n"])
    return upsert_user(db, claims)


def upsert_user(db: Session, claims: dict) -> User:
    sub, email = str(claims["sub"]), str(claims["email"]).lower()
    user = db.scalars(select(User).where(User.google_subject_id == sub)).first()
    if not user:
        user = db.scalars(select(User).where(User.email == email)).first()
        if user:
            if user.google_subject_id and user.google_subject_id != sub:
                raise GoogleAuthError("email already linked to another Google account")
            if not user.email_verified:
                user.password_hash = None      # a password set by someone who never proved they own this email must not survive linking
            user.google_subject_id = sub
        else:
            user = User(email=email, name=(claims.get("name") or email.split("@")[0])[:120], avatar_url=claims.get("picture"),
                        auth_provider="google", google_subject_id=sub, password_hash=None, is_active=True)
            db.add(user)
    if user.is_active is False:
        raise GoogleAuthError("account disabled")
    user.email_verified = True
    if not user.avatar_url and claims.get("picture"):
        user.avatar_url = claims["picture"]
    if user.email in get_settings().admin_email_set:
        user.role = "ADMIN"
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    subscriptions.ensure_subscription(db, user)
    return user
