import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Request

from .config import get_settings
from .errors import AppError

ALGO = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_token(subject: str, purpose: str = "access", minutes: int | None = None, extra: dict | None = None) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes or s.access_token_minutes)
    payload = {"sub": subject, "purpose": purpose, "exp": exp, "iss": "dreamcast", **(extra or {})}
    return jwt.encode(payload, s.auth_secret_key, algorithm=ALGO)


def decode_local_token(token: str, purpose: str = "access") -> dict:
    payload = jwt.decode(token, get_settings().auth_secret_key, algorithms=[ALGO], issuer="dreamcast")
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("wrong purpose")
    return payload


_filename_re = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str, max_len: int = 100) -> str:
    """Strip any path components and unsafe characters from a client-supplied filename."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = _filename_re.sub("_", base).strip("._") or "file"
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: max_len - len(ext) - 1] + dot + ext) if dot else base[:max_len]
    return base


class RateLimiter:
    """Tiny in-memory sliding-window limiter (single process; use Redis if you scale out)."""

    def __init__(self, limit_getter, window: int = 60):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._limit_getter, self._window = limit_getter, window

    def __call__(self, request: Request):
        limit = self._limit_getter()
        if limit <= 0:
            return
        key = f"{request.url.path}:{request.client.host if request.client else 'unknown'}"
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self._window:
            q.popleft()
        if len(q) >= limit:
            raise AppError("Too many attempts. Please wait a minute and try again.", 429, "rate_limited")
        q.append(now)

    def reset(self):
        self._hits.clear()


auth_rate_limit = RateLimiter(lambda: get_settings().rate_limit_auth_per_minute)
