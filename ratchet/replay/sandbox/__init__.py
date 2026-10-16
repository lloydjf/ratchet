"""Replay sandboxes: ``Sandbox`` protocol, ``ProcessSandbox`` (dev only, ADR 0017),
``DockerSandbox`` (ADR 0010 fallback) and ``OpenShellSandbox`` (primary)."""

from __future__ import annotations

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import ConfigError
from ratchet.replay.sandbox.base import TIMEOUT_EXIT, Endpoint, Sandbox
from ratchet.replay.sandbox.docker import DockerOptions, DockerSandbox
from ratchet.replay.sandbox.env import ALLOWED_ENV, faketime_env, scrub_env
from ratchet.replay.sandbox.openshell import OpenShellOptions, OpenShellSandbox
from ratchet.replay.sandbox.process import ProcessSandbox

__all__ = [
    "Sandbox",
    "Endpoint",
    "TIMEOUT_EXIT",
    "ProcessSandbox",
    "DockerSandbox",
    "DockerOptions",
    "OpenShellSandbox",
    "OpenShellOptions",
    "ALLOWED_ENV",
    "scrub_env",
    "faketime_env",
    "get_sandbox",
]


def get_sandbox(name: str, settings: Settings | None = None) -> Sandbox:
    """A fresh sandbox for ``RATCHET_SANDBOX`` / ``ReplayBatch.sandbox``."""
    settings = settings or get_settings()
    if name == "process":
        return ProcessSandbox(settings)
    if name == "docker":
        return DockerSandbox(settings=settings)
    if name == "openshell":
        return OpenShellSandbox(settings=settings)
    raise ConfigError(f"unknown sandbox {name!r}", code="config_error", context={"sandbox": name})
