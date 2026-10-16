"""Capture from outside: an unmodified external harness pointed at the proxy
(``OPENAI_BASE_URL=http://127.0.0.1:8081/v1``) leaves step files but no ``trace.json``; the
run is listed by ``GET /runs?unblessed=1`` and ``bless`` finalizes it into a ``live`` trace
first, then blesses it. Real proxy over ASGI, sim upstream; no network."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

from test_sim_support import SKILLS  # noqa: E402

from ratchet.capture.external import (  # noqa: E402
    EXTERNAL_HARNESS,
    captured_runs,
    finalize_captured,
    is_captured,
    skills_from_prompt,
)
from ratchet.config.settings import Settings, configure, reset_settings  # noqa: E402
from ratchet.dashboard_api.app import create_app as create_api_app  # noqa: E402
from ratchet.errors import NotFoundError  # noqa: E402
from ratchet.judge.structured import reset_structured_modes  # noqa: E402
from ratchet.models import LLMCall  # noqa: E402
from ratchet.orchestrator.bless import bless, last_unblessed  # noqa: E402
from ratchet.proxy.app import create_app as create_proxy_app  # noqa: E402
from ratchet.proxy.recorder import assistant_text  # noqa: E402
from ratchet.replay.harness.prompt import build_system_prompt  # noqa: E402
from ratchet.replay.harness.skills import load_skills, select_skills  # noqa: E402
from ratchet.store import Store  # noqa: E402
from ratchet.tools.catalog import openai_tools  # noqa: E402

FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
TASK = "Triage the open issues in acme/widgets."


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    reset_structured_modes()
    s = configure(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            ratchet_upstream="sim",
            ratchet_data_dir=tmp_path / "data",
            ratchet_skills_repo=tmp_path / "skills",
            ratchet_proxy_url="http://proxy/v1",
            ratchet_log_level="ERROR",
        )
    )
    yield s
    reset_settings()
    reset_structured_modes()


async def external_run(settings: Settings, store: Store, run_id: str | None = None) -> str:
    """What an OpenAI client does with ``base_url`` set to the proxy: plain chat calls, no
    Ratchet headers except (optionally) the run id. Returns the run id the proxy used."""
    skills = load_skills(SKILLS)
    system = build_system_prompt(skills, select_skills(TASK, skills), FROZEN)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": TASK},
    ]
    headers = {"X-Ratchet-Run-Id": run_id} if run_id else {}
    app = create_proxy_app(settings, store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://p") as client:
        body = {"model": "super", "messages": messages, "tools": openai_tools()}
        res = await client.post("/v1/chat/completions", json=body, headers=headers)
        assert res.status_code == 200, res.text
        used = res.headers["X-Ratchet-Run-Id"]
        message = res.json()["choices"][0]["message"]
        messages.append(message)
        for call in message.get("tool_calls") or []:
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": "[]"})
        body = {"model": "super", "messages": messages, "tools": openai_tools()}
        res = await client.post(
            "/v1/chat/completions", json=body, headers={"X-Ratchet-Run-Id": used}
        )
        assert res.status_code == 200, res.text
    return used


def test_skill_headers_are_read_from_the_system_prompt() -> None:
    text = "intro\n## Skill: github-triage\nbody\n## Skill: expense-report\n## Skill: Bad Name\n"
    assert skills_from_prompt(text) == ["expense-report", "github-triage"]


async def test_captured_run_finalizes_into_a_live_trace(settings: Settings) -> None:
    store = Store.open(settings.ratchet_data_dir)
    run_id = await external_run(settings, store, "run_ext_one")
    assert is_captured(store, run_id) and not store.has_trace(run_id)
    (listed,) = captured_runs(store)
    assert listed.run_id == run_id and listed.task_preview == TASK and listed.n_steps == 2

    trace = finalize_captured(store, run_id, skills_commit="abc123")
    assert trace.kind == "live" and trace.task_input == TASK
    assert trace.skills_invoked == ["github-triage"]
    assert trace.env.harness_version == EXTERNAL_HARNESS and trace.env.skills_commit == "abc123"
    assert trace.artifacts_ref is None and trace.agent_tier == "super"
    texts = [
        assistant_text(store.read_step_payload(run_id, s.response_ref), "chat")  # type: ignore[arg-type]
        for s in store.collect_steps(run_id)
        if isinstance(s, LLMCall)
    ]
    assert trace.final_output == next((t for t in reversed(texts) if t.strip()), "")
    assert store.has_trace(run_id) and not is_captured(store, run_id)
    assert captured_runs(store) == []


async def test_judge_runs_and_replays_are_not_captured_runs(settings: Settings) -> None:
    store = Store.open(settings.ratchet_data_dir)
    app = create_proxy_app(settings, store)
    body = {"model": "nano", "messages": [{"role": "user", "content": "hi"}]}
    headers = {"X-Ratchet-Run-Id": "judge_x", "X-Ratchet-Purpose": "screen"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://p") as client:
        await client.post("/v1/chat/completions", json=body, headers=headers)
    assert not is_captured(store, "judge_x")
    with pytest.raises(NotFoundError):
        finalize_captured(store, "judge_x")
    assert last_unblessed(store) is None


async def test_bless_finalizes_a_captured_run_then_blesses_it(settings: Settings) -> None:
    store = Store.open(settings.ratchet_data_dir)
    run_id = await external_run(settings, store)  # run id generated by the proxy
    assert last_unblessed(store) == run_id
    golden = await bless(store, settings, run_id)
    assert golden.kind == "golden" and golden.blessed_at is not None
    assert golden.task_input == TASK and golden.env.harness_version == EXTERNAL_HARNESS
    assert store.load_trace(run_id).kind == "golden"
    assert last_unblessed(store) is None
    assert [e.ref for e in store.events(kinds=["trace.blessed"])] == [run_id]


async def test_runs_route_lists_captured_and_live_runs_on_the_operator_host(
    settings: Settings,
) -> None:
    store = Store.open(settings.ratchet_data_dir)
    captured = await external_run(settings, store, "run_ext_two")
    blessed_id = await external_run(settings, store, "run_ext_three")
    await bless(store, settings, blessed_id)
    app = create_api_app(settings, store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        res = await client.get("/api/v1/runs", params={"unblessed": 1})
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert [(i["run_id"], i["source"], i["blessed"]) for i in items] == [
            (captured, "captured", False)
        ]
        assert items[0]["task_preview"] == TASK and items[0]["agent_tier"] == "super"
        everything = (await client.get("/api/v1/runs", params={"unblessed": 0})).json()
        assert {(i["run_id"], i["source"]) for i in everything["items"]} == {
            (captured, "captured"),
            (blessed_id, "golden"),
        }

    public = settings.model_copy(update={"ratchet_public_mode": True})
    app = create_api_app(public, store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        res = await client.get("/api/v1/runs")
    assert res.status_code == 403 and res.json()["error"]["code"] == "not_public"


def test_cli_bless_last_blesses_a_captured_run_and_logs_go_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ratchet bless --last`` picks the newest captured run; JSON log lines go to stderr so
    stdout carries only the command's own output (``serve`` keeps stdout)."""
    import asyncio

    from typer.testing import CliRunner

    from ratchet.cli.main import app
    from ratchet.log import log_stream_name, set_log_stream

    monkeypatch.setenv("RATCHET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RATCHET_SKILLS_REPO", str(tmp_path / "skills"))
    monkeypatch.setenv("RATCHET_LOG_LEVEL", "INFO")
    reset_settings()
    try:
        from ratchet.config.settings import get_settings

        cfg = get_settings()
        store = Store.open(cfg.ratchet_data_dir)
        run_id = asyncio.run(external_run(cfg, store, "run_ext_cli"))
        result = CliRunner().invoke(app, ["bless", "--last"])
        assert result.exit_code == 0, result.output
        assert result.stdout.startswith(f"Blessed {run_id}:")
        assert not [line for line in result.stdout.splitlines() if line.startswith('{"ts"')]
        assert '"event":"capture.trace.finalized"' in result.stderr
        assert log_stream_name() == "stderr"
        again = CliRunner().invoke(app, ["bless", "--last"])
        assert again.exit_code == 1 and "no live or proxy-captured runs" in again.stderr
    finally:
        set_log_stream("stdout")
        reset_settings()
