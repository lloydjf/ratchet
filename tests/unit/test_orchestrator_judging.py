"""Judging: a replay by a different adapter than its golden is never judged (ADR 0003)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import fixture_trace  # noqa: E402

from ratchet.orchestrator import judging  # noqa: E402
from ratchet.replay.harness.base import adapter_name, same_adapter  # noqa: E402


@pytest.mark.parametrize(
    ("golden", "replay", "same"),
    [
        ("minimal/0.1.0", "minimal/0.2.0", True),
        ("hermes/0.22.2", "hermes/unknown", True),
        ("minimal/0.1.0", "hermes/unknown", False),
        ("hermes/0.22.2", "minimal/sim", False),
    ],
)
def test_same_adapter_compares_the_adapter_part_only(golden: str, replay: str, same: bool) -> None:
    assert same_adapter(golden, replay) is same
    assert adapter_name("Hermes/1") == "hermes"


def test_replay_by_another_adapter_is_a_harness_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_emit(_ctx: object, kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    async def no_cascade(*_a: object, **_k: object) -> None:
        raise AssertionError("a mismatched pair must not reach the judge cascade")

    monkeypatch.setattr(judging, "emit", fake_emit)
    monkeypatch.setattr(judging, "_cascade", no_cascade)
    golden = fixture_trace("n0_cosmetic", "golden")
    replay = fixture_trace("n0_cosmetic", "replay")
    replay = replay.model_copy(
        update={"env": replay.env.model_copy(update={"harness_version": "hermes/0.22.2"})}
    )
    ctx = SimpleNamespace(work=tmp_path)
    item = judging.Replayed(golden, replay, [], None)
    pair = asyncio.run(judging._judge_one(ctx, item, "", asyncio.Semaphore(1)))  # type: ignore[arg-type]
    assert pair.outcome == "failed" and pair.error_code == "harness_mismatch"
    assert pair.replay_run_id == replay.run_id
    assert events and events[-1][1]["outcome"] == "failed"
