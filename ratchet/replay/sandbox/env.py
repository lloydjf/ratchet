"""The sandbox environment: an allowlist, the dummy key, frozen time, and a secret check.

The harness environment never holds a real key. Every sandbox builds
its env from an explicit mapping (never the worker's ``os.environ``), drops every name not
on :data:`ALLOWED_ENV`, forces the API-key variables to the dummy key, and refuses to
start if any value contains a secret held by :class:`~ratchet.config.settings.Settings`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import SecretStr

from ratchet.config.settings import Settings
from ratchet.errors import SandboxError
from ratchet.replay.harness.base import DUMMY_KEY

__all__ = [
    "ALLOWED_ENV",
    "DUMMY_KEY_VARS",
    "faketime_env",
    "secret_values",
    "scrub_env",
]

DUMMY_KEY_VARS = ("RATCHET_DUMMY_KEY", "OPENAI_API_KEY")
ALLOWED_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "RATCHET_PROXY_URL",
        "RATCHET_MOCK_TOOLS_URL",
        "RATCHET_RUN_ID",
        "RATCHET_AGENT_TIER",
        "RATCHET_LOG_LEVEL",
        "OPENAI_BASE_URL",
        "FAKETIME",
        "FAKETIME_DONT_FAKE_MONOTONIC",
        "FAKETIME_NO_CACHE",
        "LD_PRELOAD",
        "HERMES_HOME",
        "HERMES_TIMEZONE",
        *DUMMY_KEY_VARS,
    }
)
_MIN_SECRET_LEN = 8


def faketime_env(frozen_time: datetime | None, lib: str | None) -> dict[str, str]:
    """libfaketime env: start the clock at ``frozen_time`` (UTC) and let it tick."""
    if frozen_time is None or not lib:
        return {}
    stamp = frozen_time.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return {"FAKETIME": f"@{stamp}", "FAKETIME_DONT_FAKE_MONOTONIC": "1", "LD_PRELOAD": lib}


def secret_values(settings: Settings) -> list[str]:
    """Every secret value the settings hold (Token Factory, Nebius, S3, Tavily …)."""
    values: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            if len(raw) >= _MIN_SECRET_LEN and raw != DUMMY_KEY:
                values.append(raw)
    return values


def scrub_env(env: Mapping[str, str], settings: Settings) -> dict[str, str]:
    """Allowlisted, dummy-keyed, secret-free copy of ``env``.

    Raises :class:`SandboxError` (``sandbox_failed``) if a secret value would leak.
    """
    clean = {k: str(v) for k, v in env.items() if k in ALLOWED_ENV}
    preload = clean.get("LD_PRELOAD")
    if preload is not None and not preload.endswith("libfaketime.so.1"):
        del clean["LD_PRELOAD"]
    for name in DUMMY_KEY_VARS:
        clean[name] = DUMMY_KEY
    for secret in secret_values(settings):
        if any(secret in value for value in clean.values()):
            raise SandboxError(
                "refusing to start a sandbox whose environment contains a secret",
                code="sandbox_failed",
                context={"reason": "secret_in_env"},
            )
    return clean
