"""Harness adapters: the ``HarnessAdapter`` protocol, MinimalRunner, HermesAdapter.

The concrete adapters are loaded lazily so ``python -m ratchet.replay.harness.minimal``
does not import its own module twice (runpy warns when the package pre-imports it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ratchet.replay.harness.base import (
    DUMMY_KEY,
    HarnessAdapter,
    HarnessConfig,
    HostPaths,
    SandboxedHarness,
    SandboxPaths,
    SandboxPlan,
    get_adapter,
)

if TYPE_CHECKING:
    from ratchet.replay.harness.hermes import HermesAdapter
    from ratchet.replay.harness.minimal import MinimalRunner

__all__ = [
    "DUMMY_KEY",
    "HarnessAdapter",
    "HarnessConfig",
    "HostPaths",
    "SandboxPaths",
    "SandboxPlan",
    "SandboxedHarness",
    "get_adapter",
    "MinimalRunner",
    "HermesAdapter",
]


def __getattr__(name: str) -> object:
    if name == "MinimalRunner":
        from ratchet.replay.harness.minimal import MinimalRunner

        return MinimalRunner
    if name == "HermesAdapter":
        from ratchet.replay.harness.hermes import HermesAdapter

        return HermesAdapter
    raise AttributeError(name)
