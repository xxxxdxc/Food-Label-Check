"""Signed shared sessions, request limits and short database scopes."""
import hashlib
import hmac
import time
from collections import OrderedDict, deque
from threading import Lock

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie

from app.config import Settings, get_settings
from app.db import init_db
from app.errors import AppError

session_cookie = APIKeyCookie(name="session", auto_error=False)
SESSION_TTL_SECONDS = 12 * 60 * 60
_buckets = OrderedDict()
_limit_lock = Lock()


def make_session(settings: Settings) -> str:
    expires = str(int(time.time()) + SESSION_TTL_SECONDS)
    tag = hmac.new(settings.session_secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{tag}"


def require_session(token: str | None = Depends(session_cookie), settings: Settings = Depends(get_settings)) -> None:
    try:
        expires, signature = (token or "").split(".", 1)
        expected = hmac.new(settings.session_secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
        valid = int(expires) >= time.time() and hmac.compare_digest(signature.encode(), expected.encode())
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise AppError(401, "UNAUTHORIZED", "请先登录")


def rate_limit(request: Request, scope: str, limit: int, seconds: int, record=True) -> None:
    key = (scope, request.client.host if request.client else "local")
    now = time.monotonic()
    with _limit_lock:
        bucket = _buckets.setdefault(key, deque())
        _buckets.move_to_end(key)
        while bucket and bucket[0] <= now - seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise AppError(429, "RATE_LIMIT", "请求过于频繁，请稍后重试")
        if record:
            bucket.append(now)
        while len(_buckets) > 4096:
            _buckets.popitem(last=False)


def get_db():
    with init_db()() as db:
        yield db


def begin_write(db):
    from sqlalchemy import text
    db.execute(text("BEGIN IMMEDIATE"))
