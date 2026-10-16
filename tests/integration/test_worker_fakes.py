"""Test doubles for the replay worker (no tests here; loaded by path).

``make_deps()`` returns ``WorkerDeps`` whose proxy is a scripted fake OpenAI server (first
call → one mocked tool call, second call → final answer) and whose mock tool server
records ``ToolCall`` steps. Task text controls failure modes: ``SLOW`` makes the model
call hang (chaos tests), ``CRASH`` makes it return 500 (harness failure), ``SEND`` makes
the model try ``gmail.send`` (blocked with 409, logged as unexpected).

The worker loads this file with ``--deps <path>:make_deps`` in a subprocess.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ratchet import clock
from ratchet.models import EnvSnapshot, LLMCall, ReplayBatch, ToolCall, Trace
from ratchet.replay.deps import FinalizeArgs, MockAppArgs, ProxyAppArgs, RunMetaArgs, WorkerDeps
from ratchet.store import Store
from ratchet.store.archive import pack_dir
from ratchet.util import hash_json, sha256_hex

FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
FIXTURE_SKILLS = Path(__file__).resolve().parents[1] / "fixtures" / "skills"
CASSETTE_KEY = sha256_hex("github.list_issues\n" + '{"repo":"acme/widgets"}')


def _completion(message: dict[str, object]) -> dict[str, object]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


def fake_proxy_app(args: ProxyAppArgs) -> FastAPI:
    app = FastAPI()
    store = args.store

    @app.post("/v1/r/{run_id}/chat/completions")
    async def chat(run_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        task = body["messages"][1]["content"]
        if "SLOW" in task:
            await asyncio.sleep(120)
        if "CRASH" in task:
            return JSONResponse({"error": {"code": "upstream_unavailable"}}, status_code=502)
        step_id = store.next_step_id(run_id, "m")
        store.append_step(
            run_id,
            LLMCall(
                step_id=step_id,
                tier=request.headers.get("X-Ratchet-Tier", "super"),
                model="fake/super",
                request_hash=hash_json(body["messages"]),
                messages_ref=f"steps/{step_id}.req.json",
                response_ref=f"steps/{step_id}.res.json",
                prompt_tokens=100,
                completion_tokens=10,
                usd=0.001,
                latency_ms=1,
                started_at=clock.now(),
            ),
        )
        tool_results = [m for m in body["messages"] if m["role"] == "tool"]
        if not tool_results:
            tool = "gmail__send" if "SEND" in task else "github__list_issues"
            args = {"repo": "acme/widgets"} if "SEND" not in task else {"to": ["x@example.com"]}
            call = {"id": "call_1", "type": "function"}
            call["function"] = {"name": tool, "arguments": json.dumps(args)}
            return JSONResponse(
                _completion({"role": "assistant", "content": None, "tool_calls": [call]})
            )
        return JSONResponse(
            _completion({"role": "assistant", "content": f"Done: {tool_results[-1]['content']}"})
        )

    return app


def fake_mock_app(args: MockAppArgs) -> FastAPI:
    app = FastAPI()
    store = args.store
    unexpected: list[ToolCall] = []

    def record(run_id: str, tool: str, call_args: dict[str, object], **kw: object) -> ToolCall:
        call = ToolCall(
            step_id=store.next_step_id(run_id, "t"),
            tool=tool,
            args_norm=call_args,
            args_hash=hash_json(call_args),
            result_hash=None,
            side_effect=kw.get("side_effect", "read"),  # type: ignore[arg-type]
            source=kw.get("source", "cassette"),  # type: ignore[arg-type]
            cassette_key=CASSETTE_KEY if tool == "github.list_issues" else None,
            blocked=bool(kw.get("blocked", False)),
            started_at=clock.now(),
        )
        store.append_step(run_id, call)
        return call

    @app.post("/tools/{tool}")
    async def call_tool(tool: str, request: Request) -> JSONResponse:
        run_id = request.headers["X-Ratchet-Run-Id"]
        payload = await request.json()
        if tool == "gmail.send":
            unexpected.append(
                record(run_id, tool, payload["args"], side_effect="external_send", blocked=True)
            )
            return JSONResponse({"error": {"code": "ratchet_blocked"}}, status_code=409)
        record(run_id, tool, payload["args"])
        return JSONResponse({"ok": True, "result": {"issues": [{"number": 1}, {"number": 2}]}})

    @app.post("/tools/{tool}/notify")
    async def notify(tool: str, request: Request) -> JSONResponse:
        payload = await request.json()
        record(request.headers["X-Ratchet-Run-Id"], tool, payload["args"], source="local")
        return JSONResponse({"ok": True})

    @app.get("/log")
    async def log(run_id: str | None = None) -> JSONResponse:
        items = [c.model_dump(mode="json") for c in unexpected]
        return JSONResponse({"items": items})

    return app


def fake_write_run_meta(store: Store, args: RunMetaArgs) -> None:
    path = store.trace_dir(args.run_id) / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": args.run_id,
                "agent_tier": args.agent_tier,
                "seed": args.seed,
                "harness": args.harness,
            }
        )
    )


def fake_finalize(store: Store, args: FinalizeArgs) -> Trace:
    trace = Trace(
        run_id=args.run_id,
        kind="replay",
        task_input=args.task_input,
        steps=store.collect_steps(args.run_id),
        final_output=args.final_output,
        artifacts_ref=None,
        skills_invoked=args.skills_invoked,
        world_dependent=args.world_dependent,
        world_facts=[],
        env=args.env,
        agent_tier=args.agent_tier,
        seed=args.seed,
        created_at=clock.now(),
        golden_of=args.golden_of,
        candidate_commit=args.candidate_commit,
        harness_ok=args.error_code is None,
        error_code=args.error_code,
        task_label=args.task_label,
    )
    store.save_trace(trace)
    return trace


def make_deps() -> WorkerDeps:
    return WorkerDeps(
        proxy_app=fake_proxy_app,
        mock_app=fake_mock_app,
        write_run_meta=fake_write_run_meta,
        finalize_trace=fake_finalize,
    )


def make_golden(store: Store, run_id: str, task: str, *, workspace: dict[str, str]) -> Trace:
    """A golden with one cassette-served tool call, a workspace snapshot and a cassette."""
    from ratchet.models import Cassette

    ws = store.trace_dir(run_id) / "ws"
    for rel, text in workspace.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    pack_dir(ws, store.trace_dir(run_id) / "workspace.tar.zst")
    call = ToolCall(
        step_id="t001",
        tool="github.list_issues",
        args_norm={"repo": "acme/widgets"},
        args_hash=hash_json({"repo": "acme/widgets"}),
        result_hash=None,
        side_effect="read",
        source="cassette",
        cassette_key=CASSETTE_KEY,
    )
    store.save_cassette(
        CASSETTE_KEY,
        Cassette(
            tool="github.list_issues",
            args_norm={"repo": "acme/widgets"},
            response={"issues": []},
            recorded_at=FROZEN,
            side_effect="read",
        ),
    )
    golden = Trace(
        run_id=run_id,
        kind="golden",
        task_input=task,
        steps=[call],
        final_output="Done",
        artifacts_ref=None,
        skills_invoked=["triage-issues"],
        world_dependent=False,
        world_facts=[],
        env=EnvSnapshot(
            workspace_ref="workspace.tar.zst",
            frozen_time=FROZEN,
            harness_version="minimal/0.1.0",
            skills_commit="abc123",
        ),
        agent_tier="super",
        seed=7,
        created_at=FROZEN,
        golden_of=None,
        candidate_commit=None,
    )
    store.save_trace(golden)
    return golden


def make_spec(
    golden_ids: list[str],
    *,
    batch_id: str = "batch_t1",
    timeout_s: int = 120,
    skills: Path = FIXTURE_SKILLS,
) -> ReplayBatch:
    return ReplayBatch(
        batch_id=batch_id,
        edit_id="edit_t1",
        golden_run_ids=golden_ids,
        skills_tar_ref=str(skills),
        cassettes_ref="",
        worker_image="ratchet-worker:dev",
        agent_tier="super",
        sandbox="process",
        timeout_s=timeout_s,
        candidate_commit="def456",
        harness="minimal",
    )
