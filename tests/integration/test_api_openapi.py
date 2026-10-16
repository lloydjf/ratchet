"""OpenAPI export: the committed schemas/openapi.json matches the app (drift check).

Regenerate with ``ratchet api export-openapi > schemas/openapi.json`` (or
``python -c "from ratchet.dashboard_api.openapi import render_openapi as r; print(r(), end='')"``).
"""

from __future__ import annotations

import json
from pathlib import Path

from ratchet.config.settings import reset_settings
from ratchet.dashboard_api.openapi import export_openapi, render_openapi

COMMITTED = Path(__file__).resolve().parents[2] / "schemas" / "openapi.json"


def teardown_function() -> None:
    reset_settings()


def test_committed_openapi_matches_the_app() -> None:
    assert COMMITTED.exists(), "schemas/openapi.json is missing; export it"
    assert COMMITTED.read_text(encoding="utf-8") == render_openapi(), (
        "schemas/openapi.json is stale; re-export it"
    )


def test_schema_lists_v1_routes_only_and_contract_models() -> None:
    schema = export_openapi()
    paths = set(schema["paths"])
    assert "/api/v1/verdicts/{verdict_id}" in paths and "/healthz" in paths
    assert not any(p.startswith("/api/") and not p.startswith("/api/v1/") for p in paths)
    names = set(schema["components"]["schemas"])
    for model in (
        "Meta",
        "VerdictSummary",
        "VerdictView",
        "PairView",
        "EditView",
        "TraceSummary",
        "CostTile",
        "CostsView",
        "TickerView",
        "Page_VerdictSummary_",
        "ErrorEnvelope",
    ):
        assert model in names
    json.dumps(schema)  # serializable
