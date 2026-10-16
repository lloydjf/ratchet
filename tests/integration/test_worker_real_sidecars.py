"""Worker with the real capture proxy, mock tool server and ``capture.runs``.

Only Token Factory is faked (an ASGI upstream behind the proxy). Checks the full replay
path: cassette hit served by the mock server, steps captured by the proxy, the real key
reaching the upstream from the in-process proxy while the sandbox env holds only the
dummy key, and Level 0's raw material (a blocked send in ``unexpected_by_run``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ratchet.config.settings import Settings
from ratchet.models import Cassette, EnvSnapshot, ReplayBatch, ReplayResult, ToolCall, Trace
from ratchet.replay import worker
from ratchet.replay.deps import WorkerDeps, default_deps
from ratchet.replay.packing import pack_batch_inputs
from ratchet.replay.sandbox import ProcessSandbox
from ratchet.store import Store
from ratchet.store.archive import pack_dir
from ratchet.util import hash_json

pytest.importorskip("ratchet.proxy.app")
pytest.importorskip("ratchet.mocktools.app")
pytest.importorskip("ratchet.capture.runs")

FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
REAL_KEY = "tf-real-secret-key-0123456789"
SKILLS = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def _upstream(seen: list[dict[str, str]]) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        seen.append({"authorization": request.headers.get("authorization", "")})
        body = await request.json()
        task = body["messages"][1]["content"]
        tools = [m for m in body["messages"] if m["role"] == "tool"]
        if not tools:
            name, args = ("github__list_issues", {"repo": "acme/widgets", "state": "open"})
            if "email" in task:
                name, args = ("gmail__send", {"to": ["a@example.com"], "subject": "s", "body": "b"})
            fn = {"name": name, "arguments": json.dumps(args)}
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": fn}],
            }
        else:
            message = {"role": "assistant", "content": "Result: " + tools[-1]["content"]}
        return JSONResponse(
            {
                "id": "x",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
            }
        )

    return app


class _RecordingSandbox(ProcessSandbox):
    envs: list[dict[str, str]] = []

    def run(self, argv, timeout_s):  # type: ignore[no-untyped-def]
        _RecordingSandbox.envs.append(self.env)
        return super().run(argv, timeout_s)


def _deps(seen: list[dict[str, str]]) -> WorkerDeps:
    from ratchet.proxy.app import create_app

    base = default_deps()
    transport = httpx.ASGITransport(app=_upstream(seen))
    return WorkerDeps(
        proxy_app=lambda a: create_app(
            settings=a.settings, store=a.store, upstream_transport=transport, sidecar=a.sidecar
        ),
        mock_app=base.mock_app,
        write_run_meta=base.write_run_meta,
        finalize_trace=base.finalize_trace,
        sandbox_factory=lambda name, s: _RecordingSandbox(s),
    )


def _golden(store: Store, run_id: str, task: str) -> Trace:
    from ratchet.mocktools.normalize import cassette_key, normalize_args

    args = normalize_args("github.list_issues", {"repo": "acme/widgets", "state": "open"})
    key = cassette_key("github.list_issues", args)
    response = {"ok": True, "result": {"issues": [{"number": 7, "title": "Crash"}]}}
    store.save_cassette(
        key,
        Cassette(
            tool="github.list_issues",
            args_norm=args,
            response=response,
            recorded_at=FROZEN,
            side_effect="read",
        ),
    )
    ws = store.trace_dir(run_id) / "ws"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("acme widgets\n")
    pack_dir(ws, store.trace_dir(run_id) / "workspace.tar.zst")
    call = ToolCall(
        step_id="t001",
        tool="github.list_issues",
        args_norm=args,
        args_hash=hash_json(args),
        result_hash=None,
        side_effect="read",
        source="cassette",
        cassette_key=key,
    )
    golden = Trace(
        run_id=run_id,
        kind="golden",
        task_input=task,
        steps=[call],
        final_output="ok",
        artifacts_ref=None,
        skills_invoked=[],
        world_dependent=False,
        world_facts=[],
        env=EnvSnapshot(
            workspace_ref="workspace.tar.zst",
            frozen_time=FROZEN,
            harness_version="minimal/0.1.0",
            skills_commit="abc",
        ),
        agent_tier="super",
        seed=11,
        created_at=FROZEN,
        golden_of=None,
        candidate_commit=None,
    )
    store.save_trace(golden)
    return golden


def test_worker_replays_through_real_proxy_and_mock_tools(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control")
    _golden(store, "run_gold1", "Triage the open issues in acme/widgets")
    _golden(store, "run_gold2", "Triage and email the summary")
    spec = ReplayBatch(
        batch_id="batch_real",
        edit_id="edit_real",
        golden_run_ids=["run_gold1", "run_gold2"],
        skills_tar_ref=str(SKILLS),
        cassettes_ref="",
        worker_image="w",
        agent_tier="super",
        sandbox="process",
        timeout_s=120,
        candidate_commit="c0ffee",
        harness="minimal",
    )
    inputs = pack_batch_inputs(store, spec, tmp_path / "in")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        ratchet_upstream="live",
        nebius_token_factory_key=REAL_KEY,
        ratchet_data_dir=tmp_path / "wd",
    )
    seen: list[dict[str, str]] = []
    out = tmp_path / "out"

    code = worker.main(inputs, out, deps=_deps(seen), settings=settings)

    result = ReplayResult.model_validate_json((out / "result.json").read_text())
    assert code == 0, result.failed_goldens
    first, second = result.replays
    assert first.golden_of == "run_gold1" and first.candidate_commit == "c0ffee"
    tools = [s for s in first.steps if s.kind == "tool"]
    assert [t.tool for t in tools] == ["github.list_issues"] and tools[0].source == "cassette"
    assert sum(1 for s in first.steps if s.kind == "llm") == 2
    assert "Crash" in first.final_output
    blocked = result.unexpected_by_run[second.run_id]
    assert [c.tool for c in blocked] == ["gmail.send"]
    assert seen and all(h["authorization"] == f"Bearer {REAL_KEY}" for h in seen)
    for env in _RecordingSandbox.envs:
        assert REAL_KEY not in json.dumps(env)
        assert env["OPENAI_API_KEY"] == env["RATCHET_DUMMY_KEY"]


def test_worker_batch_cannot_spend_past_the_dispatch_allowance(tmp_path: Path) -> None:
    """The sidecar's scratch ledger starts empty, so the allowance from dispatch is what
    bounds the batch; with nothing left, no model call reaches the upstream."""
    store = Store.open(tmp_path / "control")
    _golden(store, "run_gold1", "Triage the open issues in acme/widgets")
    spec = ReplayBatch(
        batch_id="batch_cap",
        edit_id="edit_cap",
        golden_run_ids=["run_gold1"],
        skills_tar_ref=str(SKILLS),
        cassettes_ref="",
        worker_image="w",
        agent_tier="super",
        sandbox="process",
        timeout_s=120,
        harness="minimal",
        budget_allowance_usd={"daily": 0.0},
    )
    inputs = pack_batch_inputs(store, spec, tmp_path / "in")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        ratchet_upstream="live",
        nebius_token_factory_key=REAL_KEY,
        ratchet_data_dir=tmp_path / "wd",
    )
    seen: list[dict[str, str]] = []
    code = worker.main(inputs, tmp_path / "out", deps=_deps(seen), settings=settings)
    result = ReplayResult.model_validate_json((tmp_path / "out" / "result.json").read_text())
    assert code == worker.EXIT_BUDGET
    assert result.failed_goldens == {"run_gold1": "budget_exceeded"}
    assert seen == []


def test_sandbox_env_never_holds_the_sidecar_admin_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/log and /mode are token-gated; the worker reads /log with the token (the blocked
    send still shows up as unexpected) and the token never enters the sandbox."""
    from ratchet.replay import replay_one

    token = "known-admin-token-for-this-test"
    monkeypatch.setattr(replay_one.secrets, "token_urlsafe", lambda _n: token)
    _RecordingSandbox.envs.clear()
    test_worker_replays_through_real_proxy_and_mock_tools(tmp_path)
    assert _RecordingSandbox.envs
    for env in _RecordingSandbox.envs:
        assert token not in json.dumps(env)
