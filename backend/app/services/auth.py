"""Resolves the current user from a bearer token for either auth provider."""
import logging
from datetime import datetime, timezone
from functools import lru_cache

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import Unauthorized
from ..models import User
from ..security import decode_local_token

log = logging.getLogger("dreamcast")


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{url.rstrip('/')}/auth/v1/.well-known/jwks.json")


def _decode_supabase(token: str) -> dict:
    s = get_settings()
    alg = jwt.get_unverified_header(token).get("alg", "HS256")
    if alg == "HS256":
        return jwt.decode(token, s.auth_secret_key, algorithms=["HS256"], audience="authenticated")
    key = _jwks_client(s.auth_url).get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=[alg], audience="authenticated")


def _apply_admin_email(user: User) -> None:
    if user.email.lower() in get_settings().admin_email_set:
        user.role = "ADMIN"


def user_from_token(db: Session, token: str) -> User:
    s = get_settings()
    try:
        if s.auth_provider == "supabase":
            claims = _decode_supabase(token)
            user = _provision_supabase_user(db, claims)
        else:
            claims = decode_local_token(token)
            user = db.get(User, claims["sub"])
    except (jwt.PyJWTError, KeyError):
        raise Unauthorized("Your session has expired. Please sign in again.")
    if not user or not user.is_active:
        raise Unauthorized("This account is unavailable.")
    return user


def _provision_supabase_user(db: Session, claims: dict) -> User:
    sub, email = claims["sub"], (claims.get("email") or "").lower()
    if not email:
        raise Unauthorized()
    user = db.scalars(select(User).where(User.external_id == sub)).first() or \
        db.scalars(select(User).where(User.email == email)).first()
    meta = claims.get("user_metadata") or {}
    provider = (claims.get("app_metadata") or {}).get("provider", "email")
    if not user:
        user = User(email=email, external_id=sub, auth_provider=provider,
                    name=meta.get("full_name") or meta.get("name") or email.split("@")[0],
                    avatar_url=meta.get("avatar_url") or meta.get("picture"))
        db.add(user)
    user.external_id = sub
    last = user.last_login_at
    if not last or (datetime.now(timezone.utc) - last).total_seconds() > 3600:
        user.last_login_at = datetime.now(timezone.utc)
    _apply_admin_email(user)
    db.commit()  # no-op when nothing changed
    return user
