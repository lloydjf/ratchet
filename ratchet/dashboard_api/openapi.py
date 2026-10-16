"""OpenAPI export: ``ratchet api export-openapi > schemas/openapi.json``.

The schema does not depend on settings values or on the orchestrator being importable, and
building the app touches no disk (the store opens lazily).
"""

from __future__ import annotations

import json
from typing import Any

from ratchet.config.settings import Settings

__all__ = ["export_openapi", "render_openapi"]


def export_openapi() -> dict[str, Any]:
    from ratchet.dashboard_api.app import create_app

    # model_validate skips env and .env: the schema must not depend on the machine.
    schema: dict[str, Any] = create_app(settings=Settings.model_validate({})).openapi()
    return schema


def render_openapi() -> str:
    """The committed file's exact text: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(export_openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
