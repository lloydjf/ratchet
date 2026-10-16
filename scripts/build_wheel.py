"""Hatch build hook: ship the built dashboard inside release artifacts.

Registered in ``pyproject.toml`` (``[tool.hatch.build.hooks.custom]``), so ``uv build`` runs
it for both targets:

* **sdist** — ``dashboard/dist`` must exist; it is gitignored, so pyproject lists it as an
  artifact and the sdist carries it (the wheel is built from the sdist).
* **wheel** — ``dashboard/dist`` is mapped to ``ratchet/_dashboard_dist`` inside the wheel.
  ``Settings.ratchet_dashboard_dist`` defaults to that directory when it is installed.

Editable installs (``uv sync``) are skipped: a checkout serves ``./dashboard/dist`` directly.
A release build without the SPA fails here rather than publishing a package whose dashboard
says "not built". Build it first::

    (cd dashboard && npm ci && npm run build) && uv build
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

DIST = Path("dashboard") / "dist"
PACKAGED = "ratchet/_dashboard_dist"


class DashboardDistHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name == "wheel" and version == "editable":
            return
        dist = Path(self.root) / DIST
        if not (dist / "index.html").is_file():
            raise RuntimeError(
                f"{DIST}/index.html is missing: build the dashboard before packaging "
                "(cd dashboard && npm ci && npm run build)"
            )
        if self.target_name == "wheel":
            build_data["force_include"][str(dist)] = PACKAGED
