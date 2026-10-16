"""Public-host controls (orchestrator-api-cli.md §8).

- Session: cookie ``ratchet_session`` (HttpOnly, SameSite=Lax, Secure when the public URL is
  https, 24 h). The session hash is ``sha256(cookie)[:16]``; only the hash is ever stored.
- Action token: ``HMAC-SHA256(secret, UTC date)[:24]`` hex, rotating daily. The previous
  day's token is still accepted until 01:00 UTC. Compared in constant time.
- Rate limit: in-memory token bucket, 10 actions per minute per client IP. The IP is only
  the bucket key: it is never stored, logged or hashed into anything persistent.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from starlette.responses import Response

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.errors import ForbiddenError
from ratchet.util import sha256_hex

__all__ = [
    "SESSION_COOKIE",
    "SESSION_MAX_AGE_S",
    "ACTION_TOKEN_HEADER",
    "session_hash_for",
    "new_session_cookie",
    "valid_session_cookie",
    "set_session_cookie",
    "action_token",
    "current_action_token",
    "accepted_action_tokens",
    "check_action_token",
    "TokenBucket",
]

SESSION_COOKIE = "ratchet_session"
SESSION_MAX_AGE_S = 24 * 3600
ACTION_TOKEN_HEADER = "X-Ratchet-Action-Token"
_COOKIE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_GRACE = timedelta(hours=1)


# -- session -------------------------------------------------------------------------------
def session_hash_for(cookie: str) -> str:
    return sha256_hex(cookie)[:16]


def new_session_cookie() -> str:
    return secrets.token_urlsafe(32)


def valid_session_cookie(value: str | None) -> bool:
    return value is not None and _COOKIE_RE.fullmatch(value) is not None


def set_session_cookie(response: Response, cookie: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        cookie,
        max_age=SESSION_MAX_AGE_S,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ratchet_public_url.lower().startswith("https://"),
    )


# -- action token --------------------------------------------------------------------------
def action_token(secret: str, day: date) -> str:
    digest = hmac.new(secret.encode("utf-8"), day.isoformat().encode("ascii"), hashlib.sha256)
    return digest.hexdigest()[:24]


def current_action_token(settings: Settings, at: datetime | None = None) -> str:
    moment = (at or clock.now()).astimezone(UTC)
    return action_token(settings.ratchet_action_token_secret.get_secret_value(), moment.date())


def accepted_action_tokens(settings: Settings, at: datetime | None = None) -> list[str]:
    moment = (at or clock.now()).astimezone(UTC)
    secret = settings.ratchet_action_token_secret.get_secret_value()
    tokens = [action_token(secret, moment.date())]
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if moment - midnight < _GRACE:
        tokens.append(action_token(secret, (moment - timedelta(days=1)).date()))
    return tokens


def check_action_token(
    settings: Settings, presented: str | None, at: datetime | None = None
) -> None:
    """Raise 403 ``action_token_invalid`` unless ``presented`` is today's (or, before 01:00
    UTC, yesterday's) token. Every candidate is compared, in constant time."""
    supplied = (presented or "").encode("utf-8")
    ok = False
    for token in accepted_action_tokens(settings, at):
        ok |= hmac.compare_digest(supplied, token.encode("utf-8"))
    if not ok:
        raise ForbiddenError(
            "This action needs the demo's action token. Reload the page and try again.",
            code="action_token_invalid",
        )


# -- rate limit ----------------------------------------------------------------------------
class TokenBucket:
    """``capacity`` actions per ``period_s`` per key, refilled continuously.

    In memory only; restarting the API forgets every bucket, which is fine for a demo host.
    """

    def __init__(
        self,
        capacity: int = 10,
        period_s: float = 60.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self.capacity = float(capacity)
        self.rate = capacity / period_s
        self._monotonic = monotonic
        self._max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float | None:
        """Spend one token. Returns ``None`` when allowed, else seconds until one refills."""
        now = self._monotonic()
        with self._lock:
            tokens, updated = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - updated) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return (1.0 - tokens) / self.rate
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > self._max_keys:
                self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        full_after = self.capacity / self.rate
        stale = [k for k, (_, at) in self._buckets.items() if now - at >= full_after]
        for key in stale:
            del self._buckets[key]
