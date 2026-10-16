"""The mock tools as an MCP server at ``/mcp`` (streamable HTTP, mcp 2.x SDK) for Hermes.

Optional: when the ``mcp`` package is missing :func:`build_mcp` returns ``None`` and the
plain HTTP API keeps working. Tool names replace ``.`` with ``_`` (``github_close_issue``);
Hermes then exposes them as ``mcp__<server>__github_close_issue`` (double
underscores). Local tools (``fs.*``, ``shell.run``) are not exposed. The run id comes from the
``X-Ratchet-Run-Id`` request header, which Hermes sends from ``mcp_servers.<name>.headers``
(the adapter writes a fresh config per run); ``X-Ratchet-Frozen-Time`` is honoured the same
way. The server offers no SSE stream: ``GET /mcp`` answers 405 (the app routes only
``POST``/``DELETE`` here), which the streamable-HTTP spec allows. The low-level
``Server`` is used rather than ``MCPServer`` because the input schemas come straight from
the catalog's JSON schema instead of Python signatures.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any  # Any: the mcp SDK's generics are untyped (see below)

from ratchet.errors import RatchetError
from ratchet.log import get_logger
from ratchet.mocktools.engine import MockToolEngine
from ratchet.tools.catalog import CATALOG, ToolSpec, mcp_name

__all__ = [
    "McpMount",
    "HeaderParser",
    "build_mcp",
    "mcp_name",
    "mcp_tool_map",
    "RUN_ID_HEADER",
]

# Any below: the SDK's context/server generics are parametrized by an untyped lifespan
# state; TODO(WS3-mcp): drop once mcp exports a concrete default alias.
RUN_ID_HEADER = "x-ratchet-run-id"
FROZEN_TIME_HEADER = "x-ratchet-frozen-time"
_log = get_logger("mocktools")

HeaderParser = Callable[[str | None, str | None], tuple[str, datetime | None]]


def mcp_tool_map() -> dict[str, ToolSpec]:
    """MCP tool name -> catalog entry, for every tool the mock server serves."""
    return {mcp_name(spec.name): spec for spec in CATALOG.values() if not spec.local}


@dataclass(frozen=True)
class McpMount:
    """The ASGI endpoint to route at ``/mcp`` and the lifespan that must wrap the app."""

    asgi: Any  # Any: the SDK's ASGI app type; removed when mcp ships typed ASGI aliases.
    lifespan: Callable[[], AbstractAsyncContextManager[None]]


def _text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def build_mcp(
    engine: MockToolEngine, parse_headers: HeaderParser, *, allowed_hosts: list[str] | None = None
) -> McpMount | None:
    """Build the MCP endpoint, or ``None`` if the SDK is unavailable."""
    try:
        import mcp.types as types
        from mcp.server import ServerRequestContext
        from mcp.server.lowlevel import Server
        from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError:
        _log.warning("mocktools.mcp.unavailable")
        return None

    tools = mcp_tool_map()

    async def list_tools(
        ctx: ServerRequestContext[Any],  # Any: SDK generic
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        listed = [
            types.Tool(
                name=name,
                description=spec.description,
                input_schema={
                    "type": "object",
                    "properties": spec.params,
                    "required": list(spec.required),
                },
            )
            for name, spec in sorted(tools.items())
        ]
        return types.ListToolsResult(tools=listed)

    def failure(code: str, message: str) -> types.CallToolResult:
        body = {"error": {"code": code, "message": message}}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=_text(body))],
            structured_content=body,
            is_error=True,
        )

    async def call_tool(
        ctx: ServerRequestContext[Any],
        params: types.CallToolRequestParams,  # Any: SDK generic
    ) -> types.CallToolResult:
        spec = tools.get(params.name)
        if spec is None:
            return failure("unknown_tool", f"unknown tool {params.name}")
        headers = getattr(ctx.request, "headers", None)
        try:
            run_id, frozen = parse_headers(
                headers.get(RUN_ID_HEADER) if headers is not None else None,
                headers.get(FROZEN_TIME_HEADER) if headers is not None else None,
            )
            answer = await asyncio.to_thread(
                engine.call,
                spec.name,
                dict(params.arguments or {}),
                run_id=run_id,
                frozen_time=frozen,
            )
        except RatchetError as exc:
            return failure(exc.code, exc.message)
        if answer.status >= 400:
            error = answer.body.get("error")
            err = error if isinstance(error, dict) else {}
            return failure(str(err.get("code", "tool_error")), str(err.get("message", "")))
        result = answer.body.get("result")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=_text(result))],
            structured_content=result if isinstance(result, dict) else {"result": result},
        )

    server: Server[Any] = Server(  # Any: SDK lifespan-state generic, untyped upstream
        "ratchet-mock-tools", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool
    )
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *(allowed_hosts or [])]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts],
    )
    server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )
    manager = server.session_manager

    @asynccontextmanager
    async def lifespan() -> AsyncIterator[None]:
        async with manager.run():
            yield

    return McpMount(asgi=StreamableHTTPASGIApp(manager), lifespan=lifespan)
