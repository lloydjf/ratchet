"""MCP smoke test: the mock tools over streamable HTTP at /mcp, as Hermes would call them."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from ratchet.config.settings import Settings
from ratchet.mocktools.app import create_app
from ratchet.mocktools.mcp_server import mcp_name, mcp_tool_map
from ratchet.mocktools.world import World
from ratchet.models import ToolCall
from ratchet.store import Store

mcp = pytest.importorskip("mcp")
httpx2 = pytest.importorskip("httpx2")

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "world" / "sample"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Store]]:
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "data")  # type: ignore[call-arg]
    store = Store.open(settings.ratchet_data_dir)
    app = create_app(
        settings, store, mode="record", world=World.load(SAMPLE), tokenizer=lambda v: v
    )
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert srv.started
    yield f"http://127.0.0.1:{port}", store
    srv.should_exit = True
    thread.join(timeout=10)


def test_mcp_names_replace_dots_and_skip_local_tools() -> None:
    names = mcp_tool_map()
    assert "github_close_issue" in names and mcp_name("gmail.send") == "gmail_send"
    assert not any(name.startswith(("fs_", "shell_")) for name in names)


async def _session(base: str, run_id: str | None):  # type: ignore[no-untyped-def]
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"X-Ratchet-Run-Id": run_id} if run_id else {}
    http = httpx2.AsyncClient(headers=headers, timeout=10)
    return Client(streamable_http_client(f"{base}/mcp", http_client=http))


async def test_mcp_lists_and_calls_tools_with_the_run_header(server: tuple[str, Store]) -> None:
    base, store = server
    async with await _session(base, "run_mcp") as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert {"github_list_issues", "gmail_send", "time_now"} <= names
        result = await client.call_tool("github_list_issues", {"repo": "acme/widgets"})
        assert not result.is_error
        assert result.structured_content is not None
        assert [i["number"] for i in result.structured_content["issues"]] == [11, 12, 13]
        missing = await client.call_tool(
            "github_close_issue", {"repo": "acme/widgets", "issue": 99}
        )
        assert missing.is_error
    [first, second] = [s for s in store.collect_steps("run_mcp") if isinstance(s, ToolCall)]
    assert first.tool == "github.list_issues" and first.source == "live"
    assert second.tool == "github.close_issue"


async def test_mcp_call_without_run_header_is_a_tool_error(server: tuple[str, Store]) -> None:
    base, _ = server
    async with await _session(base, None) as client:
        result = await client.call_tool("time_now", {})
        assert result.is_error
        assert "missing_run_id" in result.content[0].text  # type: ignore[union-attr]


def test_get_mcp_is_405_so_clients_open_no_sse_stream(server: tuple[str, Store]) -> None:
    """A GET would open an SSE stream that never ends (Hermes waited 5.5 s on one)."""
    import httpx

    base, _ = server
    started = time.monotonic()
    got = httpx.get(f"{base}/mcp", headers={"Accept": "text/event-stream"}, timeout=5)
    assert got.status_code == 405 and got.headers["allow"] == "POST, DELETE"
    assert httpx.head(f"{base}/mcp", timeout=5).status_code == 405
    assert time.monotonic() - started < 2


def test_control_plane_mock_serves_mcp_for_hermes(tmp_path: Path) -> None:
    """Live runs with harness=hermes reach the mock tools only through /mcp."""
    import httpx

    from ratchet.orchestrator.agent import control_plane

    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "data")  # type: ignore[call-arg]
    store = Store.open(settings.ratchet_data_dir)
    with control_plane(settings, store, World.load(SAMPLE)) as plane:
        assert httpx.get(f"{plane.mock_url}/mcp", timeout=5).status_code == 405  # routed
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        }
        headers = {"Accept": "application/json, text/event-stream"}
        posted = httpx.post(f"{plane.mock_url}/mcp", json=init, headers=headers, timeout=5)
        assert posted.status_code == 200 and posted.json()["result"]["serverInfo"]


def test_sidecar_mcp_accepts_the_sandbox_connect_host_only_when_allowed(tmp_path: Path) -> None:
    """Inside a Docker/OpenShell sandbox Hermes calls the sidecar by the gateway address;
    the replay worker passes it as an allowed host (``MockAppArgs.mcp_allowed_hosts``)."""
    from fastapi.testclient import TestClient

    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "data")  # type: ignore[call-arg]
    store = Store.open(settings.ratchet_data_dir)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    statuses = []
    for allowed in (None, ["172.30.0.1:*"]):
        app = create_app(
            settings, store, mode="replay", world=World.load(SAMPLE), mcp_allowed_hosts=allowed
        )
        with TestClient(app, base_url="http://172.30.0.1:8082") as client:
            statuses.append(client.post("/mcp", json=init, headers=headers).status_code)
    assert statuses[0] >= 400 and statuses[1] == 200
