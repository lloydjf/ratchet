"""Tool routing for the MinimalRunner: mocked tools over HTTP, local tools in the workspace.

Mocked (side-effecting or world-facing) tools are POSTed to ``<mock>/tools/<tool>`` with
the run id header; a 409 ``ratchet_blocked`` becomes :data:`BLOCKED_RESULT` so the model
learns the action is not permitted. Local tools run against the workspace; local reads
are reported to ``<mock>/tools/<tool>/notify`` (recorded with ``source="local"``) while
writes and deletes are reconciled from the workspace diff. ``time.now`` answers the
frozen time when one is configured.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

import httpx

from ratchet import clock
from ratchet.llm.client import HEADER
from ratchet.replay.harness.base import HarnessConfig
from ratchet.replay.harness.local_tools import LocalTools
from ratchet.tools.catalog import CATALOG, LOCAL_TOOLS

__all__ = ["BLOCKED_RESULT", "FROZEN_TIME_HEADER", "ToolRouter", "error_code"]

FROZEN_TIME_HEADER = "X-Ratchet-Frozen-Time"  # mock tool server: serve time-relative data

BLOCKED_RESULT: dict[str, object] = {"error": "blocked by Ratchet: action not permitted in replay"}
_TIMEOUT_S = 120.0

JSON = dict[str, Any]  # Any: decoded JSON from the tool server


def error_code(response: httpx.Response) -> str | None:
    """``error.code`` from a Ratchet error body, if present."""
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return str(error["code"])
    return None


class ToolRouter:
    def __init__(
        self,
        client: httpx.Client,
        config: HarnessConfig,
        event: Callable[..., None],
    ) -> None:
        self._client = client
        self._cfg = config
        self._event = event

    def _headers(self) -> dict[str, str]:
        headers = {HEADER.RUN_ID: self._cfg.run_id}
        if self._cfg.frozen_time is not None:
            headers[FROZEN_TIME_HEADER] = clock.iso(self._cfg.frozen_time)
        return headers

    def dispatch(self, tool: str, args: JSON) -> JSON:
        if tool not in CATALOG:
            self._event("tool.unknown")
            return {"error": "unknown tool"}
        if tool == "time.now" and self._cfg.frozen_time is not None:
            result: JSON = {"now": clock.iso(self._cfg.frozen_time)}
            self.notify(tool, args, result)
            return result
        if tool in LOCAL_TOOLS:
            result = LocalTools(self._cfg.workspace).call(tool, args)
            self._event("tool.local", tool=tool, error="error" in result)
            if tool in LocalTools.READS:
                self.notify(tool, args, result)
            return result
        return self.mock_call(tool, args)

    def mock_call(self, tool: str, args: JSON) -> JSON:
        try:
            response = self._client.post(
                f"{self._cfg.mock_tools_url}/tools/{tool}",
                json={"args": args},
                headers=self._headers(),
                timeout=_TIMEOUT_S,
            )
        except httpx.HTTPError:
            self._event("tool.mock", tool=tool, status=0)
            return {"error": "tool server unreachable"}
        self._event("tool.mock", tool=tool, status=response.status_code)
        if response.status_code == 409 and error_code(response) == "ratchet_blocked":
            return dict(BLOCKED_RESULT)
        if response.status_code >= 400:
            return {"error": f"tool failed with status {response.status_code}"}
        try:
            payload = response.json()
        except ValueError:
            return {"error": "tool returned malformed JSON"}
        if isinstance(payload, dict) and "result" in payload:
            result = payload["result"]
            return result if isinstance(result, dict) else {"result": result}
        return payload if isinstance(payload, dict) else {"result": payload}

    def notify(self, tool: str, args: JSON, result: JSON) -> None:
        with contextlib.suppress(httpx.HTTPError):
            self._client.post(
                f"{self._cfg.mock_tools_url}/tools/{tool}/notify",
                json={"args": args, "result": result},
                headers=self._headers(),
                timeout=10.0,
            )
