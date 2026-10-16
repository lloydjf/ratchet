"""Prompt-cache share: usage parsing (both shapes), the ledger column and its migration, the
simulator's stateless hit count, ``ratchet status`` text, proxy counters and the bench line."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_fixtures import small_results  # noqa: E402

from ratchet.bench.report import ReportInputs, render  # noqa: E402
from ratchet.bench.results import EditResult, with_prompt_tokens  # noqa: E402
from ratchet.cli.report import cache_share_text  # noqa: E402
from ratchet.proxy.metrics import ProxyMetrics  # noqa: E402
from ratchet.proxy.recorder import cached_prompt_tokens, usage_from  # noqa: E402
from ratchet.sim import usage as sim_usage  # noqa: E402
from ratchet.sim.app import complete  # noqa: E402
from ratchet.store import CostRow, Store, TierTotals  # noqa: E402
from ratchet.store import db as store_db  # noqa: E402

AGENT = {"x-ratchet-purpose": "agent"}


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 100, "prompt_cache_hit_tokens": 60, "prompt_cache_miss_tokens": 40}, 60),
        ({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 32}}, 32),
        (  # the first field present wins
            {"prompt_cache_hit_tokens": 0, "prompt_tokens_details": {"cached_tokens": 32}},
            0,
        ),
        ({"prompt_tokens": 100, "prompt_tokens_details": {}}, 0),
        ({"prompt_tokens": 100}, 0),
        ({"input_tokens": 9, "input_tokens_details": {"cached_tokens": 4}}, 4),
    ],
)
def test_cached_prompt_tokens_reads_both_usage_shapes(
    usage: dict[str, object], expected: int
) -> None:
    assert cached_prompt_tokens(usage) == expected


def test_usage_from_carries_the_hits_and_clamps_them_to_the_prompt() -> None:
    chat = usage_from(
        {"usage": {"prompt_tokens": 50, "completion_tokens": 5, "prompt_cache_hit_tokens": 20}},
        {},
        "chat",
    )
    assert (chat.prompt_tokens, chat.cached_prompt_tokens) == (50, 20)
    odd = usage_from(
        {"usage": {"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 99}}}, {}, "chat"
    )
    assert odd.cached_prompt_tokens == 10
    responses = usage_from(
        {"usage": {"input_tokens": 8, "input_tokens_details": {"cached_tokens": 3}}},
        {},
        "responses",
    )
    assert responses.cached_prompt_tokens == 3
    estimated = usage_from({"choices": [{"message": {"content": "hi"}}]}, {}, "chat")
    assert estimated.estimated and estimated.cached_prompt_tokens == 0


def _old_db(data_dir: Path) -> None:
    """A database at schema version 3: ``costs`` without ``cached_prompt_tokens``."""
    data_dir.mkdir(parents=True)
    conn = sqlite3.connect(data_dir / "ratchet.db", isolation_level=None)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for index, script in enumerate(store_db._MIGRATIONS[:3], start=1):
        conn.executescript(
            "BEGIN;" + script + f"INSERT INTO schema_version VALUES ({index});COMMIT;"
        )
    conn.execute(
        """INSERT INTO costs (call_id, run_id, edit_id, tier, model, prompt_tokens,
               completion_tokens, usd, purpose, ts) VALUES
               ('old', 'r', NULL, 'super', 'm', 400, 10, 0.01, 'agent', '2026-10-16T00:00:00Z')"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(costs)")}
    conn.close()
    assert "cached_prompt_tokens" not in columns


def test_an_old_database_opens_and_records_prompt_cache_hits(tmp_path: Path) -> None:
    _old_db(tmp_path / "data")
    store = Store.open(tmp_path / "data")
    store.record_cost(
        CostRow("new", "r", None, "super", "m", 600, 10, 0.02, "agent", cached_prompt_tokens=300)
    )
    store.record_cost(CostRow("n1", "r", None, "nano", "m", 100, 1, 0.0, "screen"))
    totals = store.spend()
    assert totals.prompt_tokens["super"] == 1000
    assert totals.cached_prompt_tokens == {"nano": 0, "super": 300, "ultra": 0}
    assert totals.cache_share == pytest.approx(300 / 1100)
    assert totals.tier_cache_share("super") == pytest.approx(0.3)
    assert totals.tier_cache_share("nano") == 0.0
    assert totals.tier_cache_share("ultra") is None


def test_cache_share_is_none_without_prompt_tokens(tmp_path: Path) -> None:
    totals = Store.open(tmp_path / "data").spend()
    assert totals.cache_share is None
    assert cache_share_text(totals) == (
        "n/a of prompt tokens were cache hits (nano n/a, super n/a, ultra n/a)"
    )


def test_status_text_shows_the_share_per_tier() -> None:
    totals = TierTotals(
        usd={"nano": 0.0, "super": 0.0, "ultra": 0.0},
        prompt_tokens={"nano": 100, "super": 300, "ultra": 0},
        completion_tokens={"nano": 0, "super": 0, "ultra": 0},
        cached_prompt_tokens={"nano": 0, "super": 200, "ultra": 0},
    )
    assert cache_share_text(totals) == (
        "0.50 of prompt tokens were cache hits (nano 0.00, super 0.67, ultra n/a)"
    )


def _agent_body(messages: list[dict[str, object]]) -> dict[str, object]:
    tools = [{"type": "function", "function": {"name": "fs__read", "parameters": {}}}]
    return {"model": "ratchet-sim/super", "messages": messages, "tools": tools}


def test_simulator_hits_nothing_on_a_first_turn_and_the_prefix_on_a_continuation() -> None:
    first = [{"role": "system", "content": "S" * 400}, {"role": "user", "content": "Do it."}]
    usage = complete(_agent_body(first), AGENT).usage
    assert usage["prompt_cache_hit_tokens"] == 0
    assert usage["prompt_cache_miss_tokens"] == usage["prompt_tokens"]
    turn = {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]}
    later = [*first, turn, {"role": "tool", "tool_call_id": "call_1", "content": "ok"}]
    body = _agent_body(later)
    usage2 = complete(body, AGENT).usage
    hits = usage2["prompt_cache_hit_tokens"]
    assert isinstance(hits, int) and 0 < hits < usage2["prompt_tokens"]  # type: ignore[operator]
    assert hits == sim_usage.cached_prompt_tokens(_agent_body([*first, turn]))
    assert usage2["prompt_cache_miss_tokens"] == usage2["prompt_tokens"] - hits  # type: ignore[operator]
    assert complete(body, AGENT).usage == usage2  # deterministic


def test_proxy_metrics_count_prompt_and_cached_prompt_tokens() -> None:
    metrics = ProxyMetrics()
    metrics.observe(
        tier="super", purpose="agent", status="ok", prompt_tokens=100, cached_prompt_tokens=40
    )
    text = metrics.render().decode()
    assert 'ratchet_model_prompt_tokens_total{purpose="agent",tier="super"} 100.0' in text
    assert 'ratchet_model_cached_prompt_tokens_total{purpose="agent",tier="super"} 40.0' in text


def test_bench_report_has_the_prompt_cache_line() -> None:
    results = small_results()
    inputs = ReportInputs(results=results, out_dir=Path("bench/results/x"), date="2026-10-16")
    text, _ = render(inputs)
    assert (
        "Prompt-cache share: 0.32 of prompt tokens were cache hits "
        "(per tier: nano 0.00, super 0.50, ultra 0.25)."
    ) in text
    for run in results.runs.values():
        run.edits = [
            e.model_copy(update={"prompt_tokens_by_tier": {}, "cached_prompt_tokens_by_tier": {}})
            for e in run.edits
        ]
    bare, _ = render(ReportInputs(results=results, out_dir=Path("x"), date="2026-10-16"))
    assert "Prompt-cache share: not recorded (no prompt tokens in these results)." in bare


def test_with_prompt_tokens_copies_the_ledger_totals() -> None:
    base = EditResult(bench_id="b", skill="s", type="N0", expected_decision="PASS")
    totals = TierTotals(
        usd={"nano": 0.0, "super": 0.0, "ultra": 0.0},
        prompt_tokens={"nano": 1, "super": 2, "ultra": 3},
        completion_tokens={"nano": 0, "super": 0, "ultra": 0},
        cached_prompt_tokens={"nano": 0, "super": 1, "ultra": 0},
    )
    out = with_prompt_tokens(base, totals)
    assert out.prompt_tokens_by_tier == {"nano": 1, "super": 2, "ultra": 3}
    assert out.cached_prompt_tokens_by_tier["super"] == 1


def test_replay_import_carries_the_hits_from_the_trace_steps(tmp_path: Path) -> None:
    from judge_fakes import factory

    from ratchet.replay import packing

    step = factory.llm("m001", "hi").model_copy(update={"cached_prompt_tokens": 800})
    trace = factory.trace("run_r1", "replay", "Triage", [step], "done", golden_of="run_g1")
    store = Store.open(tmp_path / "data")
    packing._record_costs(store, trace, "edit_1", None)
    totals = store.spend(edit_id="edit_1")
    assert totals.cached_prompt_tokens["super"] == 800
    assert totals.tier_cache_share("super") == pytest.approx(800 / 1200)
