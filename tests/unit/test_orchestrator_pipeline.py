"""The state machine plumbing: transitions, stage timeline, failure routing."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import T0, commit_edit, make_settings, open_store, seeded_repo  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.orchestrator import pipeline  # noqa: E402
from ratchet.orchestrator.context import EditContext, Services, build_context  # noqa: E402
from ratchet.orchestrator.embed import LocalHashEmbedder  # noqa: E402
from ratchet.orchestrator.journal import (  # noqa: E402
    load_state_timings,
    stage_timeline,
    transition,
)
from ratchet.orchestrator.replaying import sandbox_refusal  # noqa: E402
from ratchet.replay.jobs import LocalJobRunner  # noqa: E402
from ratchet.store import Store  # noqa: E402


def _services(tmp_path: Path, **overrides: object) -> Services:
    settings = make_settings(tmp_path, **overrides)
    store = open_store(settings)
    runner = LocalJobRunner("process", settings=settings, work_root=tmp_path / "jobs")
    return Services(
        settings=settings,
        store=store,
        runner=runner,
        retry=runner,
        judge_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        embedder=LocalHashEmbedder(),
    )


def _edit(services: Services) -> str:
    repo = seeded_repo(services.settings)
    edit, diff = commit_edit(repo, {"github-triage/NOTE.md": "note\n"})
    services.store.save_edit(edit, diff)
    return edit.edit_id


async def test_transition_persists_state_emits_event_and_times_the_stage(tmp_path: Path) -> None:
    services = _services(tmp_path)
    edit_id = _edit(services)
    ctx = build_context(services, edit_id)
    with clock.frozen(T0):
        services.store.set_edit_state(edit_id, "selecting")
    with clock.frozen(T0 + timedelta(milliseconds=1500)):
        await transition(ctx, "selecting", "dispatching")
    assert services.store.edit_state(edit_id).state == "dispatching"
    (event,) = services.store.events(kinds=["orchestrator.state"], ref=edit_id)
    assert event.payload == {
        "edit_id": edit_id,
        "verdict_id": edit_id.replace("edit_", "verdict_"),
        "from": "selecting",
        "to": "dispatching",
        "elapsed_ms": 1500,
    }
    (timing,) = load_state_timings(ctx.work)
    assert (timing.stage, timing.elapsed_ms) == ("selecting", 1500)


async def test_a_rerun_state_replaces_its_timing(tmp_path: Path) -> None:
    services = _services(tmp_path)
    edit_id = _edit(services)
    ctx = build_context(services, edit_id)
    services.store.set_edit_state(edit_id, "judging")
    await transition(ctx, "judging", "drifting")
    services.store.set_edit_state(edit_id, "judging")
    await transition(ctx, "judging", "drifting")
    assert [t.stage for t in load_state_timings(ctx.work)] == ["judging"]


def test_stage_timeline_uses_copy_keys_and_splits_judging(tmp_path: Path) -> None:
    services = _services(tmp_path)
    store: Store = services.store
    edit_id = _edit(services)
    work = build_context(services, edit_id).work
    from ratchet.models import StageTiming
    from ratchet.store.base import write_json_atomic

    def at(ms: int) -> datetime:
        return T0 + timedelta(milliseconds=ms)

    timings = [
        StageTiming(stage="detected", started_at=T0, elapsed_ms=10),
        StageTiming(stage="dispatching", started_at=at(10), elapsed_ms=100),
        StageTiming(stage="collecting", started_at=at(110), elapsed_ms=900),
        StageTiming(stage="judging", started_at=at(1010), elapsed_ms=1000),
        StageTiming(stage="deciding", started_at=at(2010), elapsed_ms=50),
        StageTiming(stage="acting", started_at=at(2060), elapsed_ms=5),
        StageTiming(stage="notifying", started_at=at(2065), elapsed_ms=5),
    ]
    write_json_atomic(work / "stages.json", [t.model_dump(mode="json") for t in timings])
    with clock.frozen(at(1100)):
        store.emit("pair.level0", edit_id, {})
    with clock.frozen(at(1400)):
        store.emit("pair.opinion", edit_id, {"tier": "nano"})
    with clock.frozen(at(1800)):
        store.emit("pair.opinion", edit_id, {"tier": "super"})
    timeline = stage_timeline(store, edit_id, work)
    assert [(s.stage, s.elapsed_ms) for s in timeline] == [
        ("detected", 10),
        ("replaying", 1000),
        ("level0", 90),
        ("nano", 300),
        ("super", 610),
        ("summary", 50),
        ("done", 10),
    ]


async def test_a_failing_handler_routes_the_edit_to_failed_infra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = _services(tmp_path)
    edit_id = _edit(services)

    async def broken(ctx: EditContext) -> str:
        raise UpstreamError("selection backend down", code="upstream_unavailable")

    monkeypatch.setitem(pipeline.HANDLERS, "selecting", broken)
    state = await pipeline.run_edit(services, edit_id)
    assert state == "failed_infra"
    verdict = services.store.load_verdict(edit_id.replace("edit_", "verdict_"))
    assert verdict.decision == "WARN" and verdict.action == "held"
    assert verdict.reasons == ["replay_infra", "upstream_unavailable"]
    assert verdict.headline.startswith("Held:")


async def test_an_unexpected_exception_never_passes_the_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = _services(tmp_path)
    edit_id = _edit(services)

    async def crash(ctx: EditContext) -> str:
        raise KeyError("bug")

    monkeypatch.setitem(pipeline.HANDLERS, "selecting", crash)
    assert await pipeline.run_edit(services, edit_id) == "failed_infra"
    verdict = services.store.load_verdict(edit_id.replace("edit_", "verdict_"))
    assert verdict.decision == "WARN" and "internal_error" in verdict.reasons


def test_public_mode_refuses_the_process_sandbox(tmp_path: Path) -> None:
    assert sandbox_refusal(make_settings(tmp_path, ratchet_public_mode=True)) is not None
    assert sandbox_refusal(make_settings(tmp_path)) is None
    docker = make_settings(tmp_path, ratchet_public_mode=True, ratchet_sandbox="docker")
    assert sandbox_refusal(docker) is None


async def test_serve_refuses_public_mode_with_the_default_action_token_secret(
    tmp_path: Path,
) -> None:
    """A public host with the shipped dev secret fails fast with config_error."""
    from ratchet.config.settings import DEV_ACTION_TOKEN_SECRET, public_mode_refusal
    from ratchet.errors import ConfigError
    from ratchet.serve import serve

    default = make_settings(
        tmp_path,
        ratchet_public_mode=True,
        ratchet_sandbox="docker",
        ratchet_action_token_secret=DEV_ACTION_TOKEN_SECRET,
    )
    assert public_mode_refusal(default) is not None
    with pytest.raises(ConfigError) as info:
        await serve(default, watch=False)
    assert info.value.code == "config_error"
    assert info.value.context["field"] == "ratchet_action_token_secret"
    strong = make_settings(tmp_path, ratchet_public_mode=True, ratchet_action_token_secret="x" * 32)
    assert public_mode_refusal(strong) is None
    assert public_mode_refusal(make_settings(tmp_path)) is None  # operator host: no check


async def test_serve_refuses_public_mode_with_the_process_sandbox(tmp_path: Path) -> None:
    from ratchet.errors import ConfigError
    from ratchet.serve import serve

    with pytest.raises(ConfigError):
        await serve(make_settings(tmp_path, ratchet_public_mode=True), watch=False)


async def test_a_crash_is_recorded_next_to_the_attempt_and_shown_by_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live bench construction lost 84 edits as a bare ``internal_error``; the crash record
    keeps the exception type, message and frames, and ``ratchet logs --edit`` shows them."""
    import json

    from ratchet.cli.report import logs_text

    services = _services(tmp_path)
    edit_id = _edit(services)

    async def crash(ctx: EditContext) -> str:
        raise KeyError("missing batch result")

    monkeypatch.setitem(pipeline.HANDLERS, "collecting", crash)
    monkeypatch.setitem(pipeline.HANDLERS, "selecting", _passthrough("dispatching"))
    monkeypatch.setitem(pipeline.HANDLERS, "dispatching", _passthrough("collecting"))
    assert await pipeline.run_edit(services, edit_id) == "failed_infra"
    crashes = list(services.store.edit_dir(edit_id).glob("attempt_*/crash.json"))
    assert len(crashes) == 1
    info = json.loads(crashes[0].read_text())
    assert info["state"] == "collecting" and info["error_type"] == "KeyError"
    assert "missing batch result" in info["message"] and info["frames"]
    text = logs_text(services.store, edit_id)
    assert "--- crash in collecting: KeyError: 'missing batch result'" in text


def _passthrough(nxt: str):  # type: ignore[no-untyped-def]
    async def handler(ctx: EditContext) -> str:
        return nxt

    return handler
