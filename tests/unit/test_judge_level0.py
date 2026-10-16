"""Level 0 diff over the 12 fixture pairs, plus the multiset rules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import TRACES_DIR, factory  # noqa: E402

from ratchet.judge import level0  # noqa: E402
from ratchet.models import Trace  # noqa: E402

PAIRS = factory.pairs()


def test_there_are_twelve_pairs_covering_the_taxonomy() -> None:
    assert len(PAIRS) == 12
    cats = {p.category for p in PAIRS}
    assert {"N0", "R1", "R2", "R3", "R4", "R5", "R6", "D1"} <= cats


@pytest.mark.parametrize("pair", PAIRS, ids=[p.name for p in PAIRS])
def test_level0_matches_fixture_expectations(pair: object) -> None:
    finding = level0.diff(pair.golden, pair.replay, pair.unexpected)  # type: ignore[attr-defined]
    expect = pair.expect  # type: ignore[attr-defined]
    assert finding.hard_fail is expect["hard_fail"]
    assert len(finding.new_calls) == expect["n_new"]
    assert len(finding.missing_calls) == expect["n_missing"]
    assert len(finding.changed_args) == expect["n_changed"]
    assert finding.missing_hard is bool(expect.get("missing_hard", False))
    assert finding.golden_run_id == pair.golden.run_id  # type: ignore[attr-defined]


def test_new_hard_class_call_is_a_hard_fail() -> None:
    finding = level0.diff(*_parts("r4_new_send"))
    assert finding.hard_fail and finding.new_calls[0].tool == "gmail.send"


def test_cosmetic_diff_has_no_findings() -> None:
    finding = level0.diff(*_parts("n0_cosmetic"))
    assert not (finding.new_calls or finding.missing_calls or finding.changed_args)


def test_changed_args_on_hard_class_tool_is_evidence_not_hard_fail() -> None:
    finding = level0.diff(*_parts("r2_dropped_field"))
    tools = sorted(g.tool for g, _ in finding.changed_args)
    assert tools == ["gmail.send", "sheets.append"]
    assert not finding.hard_fail and not finding.new_calls and not finding.missing_calls


def test_blocked_unexpected_call_listed_in_steps_is_counted_once() -> None:
    golden, replay, unexpected = _parts("r4_new_send")
    assert len(level0.diff(golden, replay, unexpected).new_calls) == 1
    assert len(level0.diff(golden, replay, []).new_calls) == 1


def test_unexpected_call_absent_from_steps_is_added_to_replay_calls() -> None:
    golden, replay, unexpected = _parts("r4_new_send")
    stripped = replay.model_copy(update={"steps": replay.steps[:-1]})
    finding = level0.diff(golden, stripped, unexpected)
    assert finding.hard_fail and len(finding.new_calls) == 1


def test_read_calls_never_count_and_repeated_calls_are_a_multiset() -> None:
    golden, replay, _ = _parts("n0_reordered")
    write = next(s for s in golden.steps if getattr(s, "tool", "") == "github.label")
    doubled = replay.model_copy(update={"steps": [*replay.steps, write]})
    finding = level0.diff(golden, doubled, [])
    assert len(finding.new_calls) == 1 and not finding.hard_fail
    reads_only = golden.model_copy(update={"steps": golden.steps[:2]})
    assert not level0.diff(reads_only, reads_only, []).new_calls


def test_missing_irreversible_call_sets_missing_hard() -> None:
    finding = level0.diff(*_parts("r6_underfire"))
    assert finding.missing_hard and not finding.hard_fail
    assert {c.tool for c in finding.missing_calls} == {"github.label", "github.close_issue"}


def test_committed_json_fixtures_are_current() -> None:
    for name, text in factory.render_all().items():
        assert (TRACES_DIR / name).read_text(encoding="utf-8") == text, name
        data = json.loads(text)
        Trace.model_validate(data["golden"])
        Trace.model_validate(data["replay"])


def _parts(name: str) -> tuple:
    pair = factory.by_name(name)
    return pair.golden, pair.replay, list(pair.unexpected)


def test_output_similarity_ignores_whitespace_and_case_and_flags_material_change() -> None:
    from ratchet.judge.level0 import output_similarity

    assert output_similarity("", "") == 1.0
    assert output_similarity("Total: 12.50 EUR\n", "total:  12.50 eur") == 1.0
    assert output_similarity("Filed 3 receipts.", "Filed 3 receipts. VAT: 2.10 EUR") < 0.9
    assert 0.0 <= output_similarity("abc", "xyz") < 0.1
