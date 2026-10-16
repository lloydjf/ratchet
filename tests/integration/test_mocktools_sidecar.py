"""Mock tool server as a replay-worker sidecar.

The sandbox can reach the sidecar, so ``/log`` and ``/mode`` need the worker's admin
token, and tool calls / ``/notify`` are pinned to the one run the sidecar serves.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ratchet.config.settings import Settings
from ratchet.mocktools.app import ADMIN_TOKEN_HEADER, create_app
from ratchet.store import Store

TOKEN = "worker-admin-token-xyz"
RUN = {"X-Ratchet-Run-Id": "run_side"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "data")  # type: ignore[call-arg]
    store = Store.open(settings.ratchet_data_dir)
    app = create_app(
        settings,
        store,
        mode="replay",
        tokenizer=lambda v: v,  # type: ignore[arg-type]
        enable_mcp=False,
        admin_token=TOKEN,
        run_id="run_side",
    )
    return TestClient(app)


def test_sidecar_log_and_mode_need_the_admin_token(client: TestClient) -> None:
    for method, path in (("GET", "/log"), ("GET", "/mode")):
        refused = client.request(method, path)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "sidecar_token_invalid"
        allowed = client.request(method, path, headers={ADMIN_TOKEN_HEADER: TOKEN})
        assert allowed.status_code == 200
    switch = client.post("/mode", json={"mode": "record"})
    assert switch.status_code == 403
    assert client.get("/mode", headers={ADMIN_TOKEN_HEADER: TOKEN}).json()["mode"] == "replay"


def test_sidecar_refuses_tool_calls_and_notify_for_another_run(client: TestClient) -> None:
    other = {"X-Ratchet-Run-Id": "run_other"}
    notify = client.post("/tools/fs.read/notify", json={"args": {"path": "a"}}, headers=other)
    call = client.post("/tools/github.list_issues", json={"args": {"repo": "a/b"}}, headers=other)
    assert notify.status_code == 403 and call.status_code == 403
    assert notify.json()["error"]["code"] == "run_id_mismatch"
    own = client.post("/tools/fs.read/notify", json={"args": {"path": "a"}}, headers=RUN)
    assert own.status_code == 200


def test_sidecar_notify_still_refuses_mocked_tools(client: TestClient) -> None:
    forged = client.post("/tools/gmail.send/notify", json={"args": {}}, headers=RUN)
    assert forged.status_code == 400


def test_control_plane_server_keeps_admin_routes_open(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "data")  # type: ignore[call-arg]
    app = create_app(
        settings,
        Store.open(settings.ratchet_data_dir),
        mode="replay",
        tokenizer=lambda v: v,  # type: ignore[arg-type]
        enable_mcp=False,
    )
    with TestClient(app) as plain:
        assert plain.get("/log").status_code == 200
        assert plain.get("/mode").status_code == 200
