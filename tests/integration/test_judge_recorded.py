"""Judge ↔ recorded model responses and Tavily fixtures, including Super and Tavily outages.

Every fixture pair goes through Level 0 → cascade → drift → decide → narrative with a scripted
model client fed from ``tests/fixtures/model_responses`` and ``FixtureTavily``. No network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

from judge_fakes import (  # noqa: E402
    TAVILY_DIR,
    FakeModelClient,
    factory,
    pair_context,
    responses,
    settings,
)

from ratchet import copy  # noqa: E402
from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.judge import decide, drift, narrative  # noqa: E402
from ratchet.judge.cascade import judge_pair  # noqa: E402
from ratchet.judge.tavily import FixtureTavily  # noqa: E402
from ratchet.models import PairVerdict  # noqa: E402

SCREEN = responses("screen")
CHECK = responses("check")
ADJUDICATE = responses("adjudicate")
COMPARE = responses("compare_fact")
ATTRIBUTE = responses("attribute_drift")
SCREENED = sorted(SCREEN)
EXPECTED_DECISION = {
    "n0_cosmetic": "PASS",
    "n0_reordered": "PASS",
    "r1_logic": "BLOCK",
    "r2_dropped_field": "BLOCK",
    "r3_changed_label": "BLOCK",
    "r4_new_send": "BLOCK",
    "r4_new_delete": "BLOCK",
    "r5_overfire": "BLOCK",
    "r6_underfire": "BLOCK",
    "d1_fx_drift": "PASS",
    "d1_no_drift": "BLOCK",
    "inj_prompt_injection": "BLOCK",
}


def _recorded(name: str) -> FakeModelClient:
    client = FakeModelClient()
    for prompt, table in (
        ("screen", SCREEN),
        ("check", CHECK),
        ("adjudicate", ADJUDICATE),
        ("compare_fact", COMPARE),
        ("attribute_drift", ATTRIBUTE),
    ):
        if name in table:
            item = table[name]
            client.add(prompt, item["raw"] if isinstance(item, dict) else item)
    return client


async def _run(name: str, client: FakeModelClient, tavily: object | None = None) -> PairVerdict:
    pair = factory.by_name(name)
    verdict = await judge_pair(pair_context(pair), client)
    return await drift.check_pair(
        verdict, pair.golden, pair.replay, client, tavily or FixtureTavily(TAVILY_DIR)
    )


def test_ten_pairs_are_screened_and_two_are_decided_by_level0() -> None:
    assert len(SCREENED) == 10
    assert set(EXPECTED_DECISION) - set(SCREENED) == {"r4_new_send", "r4_new_delete"}


@pytest.mark.parametrize("name", SCREENED)
async def test_nano_screen_opinions_match_recordings(tmp_path: Path, name: str) -> None:
    """Nano's screen opinions match the recorded responses."""
    with settings(tmp_path):
        verdict = await _run(name, _recorded(name))
    nano = verdict.opinions[0]
    assert nano.tier == "nano" and nano.verdict == SCREEN[name]["verdict"]
    assert nano.prompt_hash and nano.usd > 0
    assert verdict.outcome == SCREEN[name]["outcome"]


@pytest.mark.parametrize("name", sorted(EXPECTED_DECISION))
async def test_edit_level_decision_per_fixture(tmp_path: Path, name: str) -> None:
    client = _recorded(name)
    with settings(tmp_path):
        pair = await _run(name, client)
        result = decide.decide([pair], n_failed=0, n_total=1, ultra_timed_out=False)
        inp = narrative.NarrativeInput(
            result.decision, result.action, factory.by_name(name).golden.skills_invoked or ["x"],
            [pair], {"nano": 0.01}, result.reasons,
        )  # fmt: skip
        story = narrative.fallback_narrative(inp)
    assert result.decision == EXPECTED_DECISION[name], (name, pair.outcome, result.reasons)
    assert story.startswith(copy.DECISION_WORDS[result.decision])
    if pair.decided_by == "level0":
        assert client.calls == []  # Ultra (or any tier) never sees Level-0-decided pairs


@pytest.mark.parametrize("name", sorted(CHECK))
async def test_super_evidence_steps_exist_in_the_traces(tmp_path: Path, name: str) -> None:
    """Every step Super cites as evidence exists in the golden or replay trace."""
    pair = factory.by_name(name)
    ids = {s.step_id for s in pair.golden.steps} | {s.step_id for s in pair.replay.steps}
    with settings(tmp_path):
        verdict = await _run(name, _recorded(name))
    sup = next(o for o in verdict.opinions if o.tier == "super")
    assert sup.evidence_steps and set(sup.evidence_steps) <= ids
    for crit in sup.criteria_met:
        assert set(crit.evidence_steps) <= ids
    if name == "r3_changed_label":  # the recording cites an invented "t042"
        assert "t042" not in sup.evidence_steps and "t042" in sup.reasons[-1]


async def test_ultra_timeout_yields_warn_judge_timeout(tmp_path: Path) -> None:
    """An Ultra timeout yields WARN ``judge_timeout``."""
    client = FakeModelClient()
    client.add("screen", SCREEN["r1_logic"]["raw"]).add("check", CHECK["r1_logic"])
    client.add("adjudicate", UpstreamError("ultra call timed out", code="judge_timeout"))
    with settings(tmp_path):
        pair = await _run("r1_logic", client)
        others = [await _run("n0_cosmetic", _recorded("n0_cosmetic"))]
    result = decide.decide([pair, *others], n_failed=0, n_total=2, ultra_timed_out=False)
    assert pair.outcome == "uncertain" and pair.error_code == "judge_timeout"
    assert (result.decision, result.action) == ("WARN", "held")
    assert result.reasons[0] == "judge_timeout"


async def test_drift_fixture_becomes_drift_and_unchanged_world_keeps_regression(
    tmp_path: Path,
) -> None:
    """The moved fact turns the pair into drift; an unchanged world keeps the regression."""
    with settings(tmp_path):
        moved = await _run("d1_fx_drift", _recorded("d1_fx_drift"))
        still = await _run("d1_no_drift", _recorded("d1_no_drift"))
    assert moved.outcome == "drift" and moved.decided_by == "drift"
    card = moved.drift[0]
    assert card.changed and card.old_value == "0.92" and card.new_value == "0.95"
    assert card.source_url == "https://fx.example.com/usd-eur" and card.attributable is True
    assert card.tavily_results_ref.startswith("fixture:")
    assert moved.reason.startswith("USD/EUR rate on 2026-10-14 was 0.92 changed since")
    assert still.outcome == "regression" and not still.drift[0].changed
    assert still.drift[0].ruling.startswith("The world did not move")
    assert still.drift[0].source_url == "https://docs.tokenfactory.nebius.com/pricing"
    result = decide.decide([moved], n_failed=0, n_total=1, ultra_timed_out=False)
    assert result.decision == "PASS" and "drift" in result.reasons


async def test_token_factory_503_on_super_still_produces_a_verdict(tmp_path: Path) -> None:
    """Super unavailable: the proxy already retried; the judge records uncertain, moves on."""
    client = FakeModelClient()
    client.add("screen", SCREEN["r2_dropped_field"]["raw"])
    client.add("check", UpstreamError("upstream status 503", code="upstream_unavailable"))
    client.add("adjudicate", UpstreamError("upstream status 503", code="upstream_unavailable"))
    client.add("narrative", UpstreamError("upstream status 503", code="upstream_unavailable"))
    with settings(tmp_path):
        pair = await _run("r2_dropped_field", client)
        result = decide.decide([pair], n_failed=0, n_total=1, ultra_timed_out=False)
        story = await narrative.write_narrative(
            narrative.NarrativeInput(
                result.decision, result.action, ["expense-report"], [pair], {}, result.reasons
            ),
            client,
        )
    assert [o.verdict for o in pair.opinions] == ["regressed", "uncertain", "uncertain"]
    assert pair.opinions[1].reasons == ["upstream_unavailable"]
    assert pair.outcome == "uncertain" and pair.error_code == "upstream_unavailable"
    assert result.decision == "WARN" and story.fallback_used and story.text.startswith("Held:")


class _DownTavily:
    async def search(self, query: str, params: object = None) -> object:
        raise UpstreamError("tavily /search failed: status 503", code="drift_unavailable")

    async def extract(self, url: str) -> str:
        raise UpstreamError("down", code="drift_unavailable")


async def test_tavily_unavailable_keeps_the_regression(tmp_path: Path) -> None:
    """With Tavily down the drift check is unavailable and the regression stands."""
    client = _recorded("d1_no_drift")
    with settings(tmp_path):
        pair = await _run("d1_no_drift", client, _DownTavily())
    assert pair.outcome == "regression" and pair.error_code == "drift_unavailable"
    card = pair.drift[0]
    assert card.status == "unavailable" and card.ruling == copy.reason_text("drift_unavailable")
    assert copy.DRIFT_UNAVAILABLE_LABEL == "drift check unavailable"
    assert client.calls_for("compare_fact") == []
    result = decide.decide([pair], n_failed=0, n_total=1, ultra_timed_out=False)
    assert result.decision == "BLOCK"  # Super and Ultra agreed; the world check could not help
