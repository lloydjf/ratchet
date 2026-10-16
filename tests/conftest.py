"""Test-suite isolation: no test may read the developer's `.env` or real credentials.

`Settings` loads `.env` by default, so without this a test that builds `Settings(...)` with
`ratchet_upstream="live"` could pick up a real Token Factory or Tavily key and spend real
credits. Tests marked `live` (manual workflow, $2 cap) are the only exception.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ratchet.config.settings import Settings, reset_settings
from ratchet.log import set_log_stream

_CREDENTIAL_VARS = (
    "NEBIUS_TOKEN_FACTORY_KEY",
    "NEBIUS_API_KEY",
    "NEBIUS_PROJECT_ID",
    "NEBIUS_S3_ACCESS_KEY_ID",
    "NEBIUS_S3_SECRET_ACCESS_KEY",
    "TAVILY_API_KEY",
    "RATCHET_NOTIFY_WEBHOOK",
)

_ORIGINAL_ENV_FILE = Settings.model_config.get("env_file")


@pytest.fixture(autouse=True)
def _isolate_settings(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.node.get_closest_marker("live") is not None:
        yield
        return
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in _CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RATCHET_UPSTREAM", "sim")
    monkeypatch.setenv("RATCHET_TAVILY_MODE", "fixtures")
    reset_settings()
    set_log_stream("stdout")  # a CLI invocation switches JSON logs to stderr process-wide
    yield
    set_log_stream("stdout")
    reset_settings()


def pytest_sessionstart(session: pytest.Session) -> None:
    # Module-level Settings() built during collection must not see `.env` either.
    Settings.model_config["env_file"] = None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    Settings.model_config["env_file"] = _ORIGINAL_ENV_FILE
