"""Drift check pieces: dynamic Tavily filtering, LiveTavily retries and cache, fixtures."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import TAVILY_DIR, FakeModelClient, factory, settings  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.judge import drift  # noqa: E402
from ratchet.judge.tavily import (  # noqa: E402
    FixtureTavily,
    LiveTavily,
    SearchParams,
    TavilyResults,
    fixture_name,
)
from ratchet.models import JudgeOpinion, PairVerdict  # noqa: E402
from ratchet.store import Store  # noqa: E402

SEARCH = "https://api.tavily.com/search"


def test_domain_comes_from_the_source_step_url() -> None:
    pair = factory.by_name("d1_no_drift")
    fact = pair.golden.world_facts[0]
    assert drift.fact_domain(fact, pair.golden) == "docs.tokenfactory.nebius.com"
    with clock.frozen(datetime(2026, 10, 15, tzinfo=UTC)):
        params = drift.search_params(fact, pair.golden)
    assert params.include_domains == ["docs.tokenfactory.nebius.com"]
    assert params.time_range == "week" and params.start_date is None
    assert params.search_depth == "advanced" and params.topic == "general"


def test_finance_facts_use_the_finance_topic_and_old_claims_use_start_date() -> None:
    pair = factory.by_name("d1_fx_drift")
    fact = pair.golden.world_facts[0]
    assert drift.fact_domain(fact, pair.golden) is None
    with clock.frozen(datetime(2026, 11, 30, tzinfo=UTC)):
        params = drift.search_params(fact, pair.golden)
    # live runs: "general" returns pages that state the value; "finance" returned quote pages
    assert params.topic == "general" and params.include_domains == []
    assert params.time_range is None and params.start_date == "2026-10-14"
    body = params.body("q")
    assert body["start_date"] == "2026-10-14" and "time_range" not in body


class _Recorder:
    def __init__(self, results: list[TavilyResults]) -> None:
        self.results = results
        self.params: list[SearchParams] = []

    async def search(self, query: str, params: SearchParams | None = None) -> TavilyResults:
        assert params is not None
        self.params.append(params)
        return self.results.pop(0)

    async def extract(self, url: str) -> str:
        return ""


async def test_empty_domain_restricted_search_is_repeated_unrestricted() -> None:
    pair = factory.by_name("d1_no_drift")
    hit = FixtureTavily(TAVILY_DIR)
    full = await hit.search(pair.golden.world_facts[0].query_hint)
    rec = _Recorder([TavilyResults("q", [], "r0"), full])
    out = await drift._search(rec, "q", SearchParams(include_domains=["a.example"]))
    assert out is full and [p.include_domains for p in rec.params] == [["a.example"], []]


@respx.mock
async def test_live_tavily_posts_rest_search_with_filters_and_caches(tmp_path: Path) -> None:
    payload = json.loads((TAVILY_DIR / "token_factory_pricing.json").read_text())
    route = respx.post(SEARCH).mock(return_value=httpx.Response(200, json=payload))
    with settings(tmp_path):
        store = Store.open(tmp_path / "data")
        live = LiveTavily("tvly-test", store)
        params = SearchParams(include_domains=["docs.tokenfactory.nebius.com"])
        first = await live.search("ultra price", params)
        second = await live.search("ultra price", params)
    assert route.call_count == 1  # cached by request + UTC day
    sent = json.loads(route.calls[0].request.content)
    assert sent["search_depth"] == "advanced" and sent["time_range"] == "week"
    assert sent["include_domains"] == ["docs.tokenfactory.nebius.com"]
    assert route.calls[0].request.headers["Authorization"] == "Bearer tvly-test"
    assert first.hits[0].url.startswith("https://docs.tokenfactory") and second.hits == first.hits
    assert (store.data_dir / first.ref).exists() and first.ref.startswith("tavily/")


@respx.mock
async def test_live_tavily_retries_twice_with_backoff_then_fails() -> None:
    route = respx.post(SEARCH).mock(return_value=httpx.Response(503))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient() as http:
        live = LiveTavily("k", None, http=http, sleep=fake_sleep)
        with pytest.raises(UpstreamError) as err:
            await live.search("q")
    assert err.value.code == "drift_unavailable"
    assert route.call_count == 3 and sleeps == [1.0, 3.0]


@respx.mock
async def test_live_tavily_recovers_after_a_timeout_and_does_not_retry_4xx() -> None:
    respx.post(SEARCH).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"results": []})]
    )

    async def no_sleep(_: float) -> None:
        return None

    live = LiveTavily("k", None, sleep=no_sleep)
    assert (await live.search("q")).hits == []
    bad = respx.post("https://api.tavily.com/extract").mock(return_value=httpx.Response(401))
    with pytest.raises(UpstreamError):
        await live.extract("https://x.example")
    assert bad.call_count == 1


@respx.mock
async def test_live_tavily_extract_returns_raw_content() -> None:
    respx.post("https://api.tavily.com/extract").mock(
        return_value=httpx.Response(200, json={"results": [{"raw_content": "page"}]})
    )
    assert await LiveTavily("k").extract("https://x.example") == "page"


async def test_fixture_tavily_reads_hash_named_and_indexed_files(tmp_path: Path) -> None:
    src = FixtureTavily(TAVILY_DIR)
    fx = await src.search("USD to EUR exchange rate today")
    assert fx.hits[0].content.startswith("1 USD = 0.95") and fx.ref.startswith("fixture:")
    assert fx.ref == f"fixture:{fixture_name('USD to EUR exchange rate today')}"
    priced = await src.search("Nebius Token Factory Nemotron 3 Ultra price per million tokens")
    assert priced.ref == "fixture:token_factory_pricing.json"
    with pytest.raises(UpstreamError):
        await src.search("never recorded")
    (tmp_path / fixture_name("down")).write_text('{"error": "503"}')
    with pytest.raises(UpstreamError):
        await FixtureTavily(tmp_path).search("down")
    assert await src.extract("https://x") == ""


def _pair_verdict(outcome: str, decided_by: str = "ultra") -> PairVerdict:
    pair = factory.by_name("d1_fx_drift")
    from ratchet.judge import level0

    return PairVerdict(
        golden_run_id=pair.golden.run_id,
        replay_run_id=pair.replay.run_id,
        level0=level0.diff(pair.golden, pair.replay, []),
        opinions=[
            JudgeOpinion(
                tier="ultra", verdict="uncertain", confidence=0.5, reasons=["x"],
                evidence_steps=[], usd=0.01,
            )
        ],
        drift=[],
        outcome=outcome,
        usd=0.01,
        decided_by=decided_by,
    )  # fmt: skip


@pytest.mark.parametrize(("outcome", "decided_by"), [("pass", "nano"), ("regression", "level0")])
async def test_ineligible_pairs_are_returned_unchanged(outcome: str, decided_by: str) -> None:
    pair = factory.by_name("d1_fx_drift")
    pv = _pair_verdict(outcome, decided_by)
    client = FakeModelClient()
    out = await drift.check_pair(pv, pair.golden, pair.replay, client, FixtureTavily(TAVILY_DIR))
    assert out is pv and client.calls == []


async def test_not_world_dependent_or_no_volatile_facts_skips() -> None:
    pair = factory.by_name("d1_fx_drift")
    stable = pair.golden.model_copy(update={"world_dependent": False})
    pv = _pair_verdict("regression")
    out = await drift.check_pair(
        pv, stable, pair.replay, FakeModelClient(), FixtureTavily(TAVILY_DIR)
    )
    assert out is pv


async def test_changed_but_not_attributable_fact_keeps_the_outcome(tmp_path: Path) -> None:
    pair = factory.by_name("d1_fx_drift")
    client = (
        FakeModelClient()
        .add("compare_fact", '{"changed": true, "new_value": "0.95", "confidence": 0.9}')
        .add("attribute_drift", '{"attributable": true, "confidence": 0.6, "reason": "partly"}')
    )
    with settings(tmp_path):
        out = await drift.check_pair(
            _pair_verdict("regression"), pair.golden, pair.replay, client, FixtureTavily(TAVILY_DIR)
        )
    assert out.outcome == "regression" and out.drift[0].attributable is False
    assert out.drift[0].changed and out.drift[0].new_value == "0.95"
    assert out.usd > 0.01


async def test_compare_failure_is_not_a_change(tmp_path: Path) -> None:
    pair = factory.by_name("d1_fx_drift")
    # Nano fails the schema three times, so Super gets a second opinion and fails too.
    client = FakeModelClient().add("compare_fact", *(["nope"] * 6))
    with settings(tmp_path):
        out = await drift.check_pair(
            _pair_verdict("uncertain"), pair.golden, pair.replay, client, FixtureTavily(TAVILY_DIR)
        )
    assert [c.tier for c in client.calls_for("compare_fact")] == ["nano"] * 3 + ["super"] * 3
    assert out.outcome == "uncertain" and not out.drift[0].changed
    assert out.drift[0].ruling == "Judge answer unreadable; treated as uncertain."


def test_search_body_requests_tavily_answer() -> None:
    body = SearchParams().body("EUR to USD exchange rate today")
    assert body["include_answer"] == "basic"


def test_parse_results_keeps_tavily_answer_and_evidence_leads_with_it() -> None:
    from ratchet.judge.drift import _results_text
    from ratchet.judge.tavily import parse_results

    payload = {
        "answer": "The EUR to USD exchange rate today is 1.1212.",
        "results": [
            {"title": "EUR/USD", "url": "https://example.com/fx", "content": "nav nav nav"}
        ],
    }
    results = parse_results("q", payload, "fixture:x.json")
    assert results.answer == "The EUR to USD exchange rate today is 1.1212."
    text = _results_text(results)
    assert text.splitlines()[0] == "Tavily answer: The EUR to USD exchange rate today is 1.1212."
    assert "https://example.com/fx" in text


def test_current_query_drops_the_observation_date() -> None:
    from ratchet.judge.drift import current_query

    assert current_query("EUR USD exchange rate 2026-10-14") == "EUR USD exchange rate today"
    assert current_query("rate on 2026-10-14 for EUR") == "rate for EUR today"
    assert current_query("EUR USD rate now 2026-10-14") == "EUR USD rate now"
    assert current_query("EUR USD exchange rate") == "EUR USD exchange rate"  # no date: as is
    assert current_query("2026-10-14") == "2026-10-14"  # nothing left: keep the hint


async def test_fixture_index_wildcard_answers_any_wording(tmp_path: Path) -> None:
    (tmp_path / "rate.json").write_text(json.dumps({"answer": "1.1630", "results": []}))
    (tmp_path / "index.json").write_text(
        json.dumps({"EUR to USD today": "rate.json", "*": "rate.json"})
    )
    results = await FixtureTavily(tmp_path).search("EUR USD exchange rate")
    assert results.answer == "1.1630"


def test_observed_facts_come_from_a_changed_world_tool_result() -> None:
    pair = factory.by_name("d1_fx_drift")  # finance.rate 0.92 in golden, 0.95 in replay
    facts = drift.observed_facts(pair.golden, pair.replay)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.value == "0.92" and fact.source_step == "t001" and fact.volatile
    assert fact.query_hint == "USD to EUR exchange rate today"


def test_observed_facts_ignore_unchanged_and_non_world_tools() -> None:
    pair = factory.by_name("d1_fx_drift")
    assert drift.observed_facts(pair.golden, pair.golden) == []  # same results: no movement
    triage = factory.by_name("r4_new_send")
    assert drift.observed_facts(triage.golden, triage.replay) == []  # no world tools


async def test_check_pair_uses_observed_facts_when_bless_found_none(tmp_path: Path) -> None:
    pair = factory.by_name("d1_fx_drift")
    golden = pair.golden.model_copy(update={"world_facts": []})
    client = (
        FakeModelClient()
        .add("compare_fact", '{"changed": true, "new_value": "0.95", "confidence": 0.9}')
        .add("attribute_drift", '{"attributable": true, "confidence": 0.9, "reason": "rate"}')
    )
    with settings(tmp_path):
        out = await drift.check_pair(
            _pair_verdict("uncertain"), golden, pair.replay, client, FixtureTavily(TAVILY_DIR)
        )
    assert [c.fact_id for c in out.drift] == ["obs_t001"]
    assert out.outcome == "drift"


def test_comparisons_must_be_grounded_in_the_evidence() -> None:
    from ratchet.judge.drift import _grounded_compare
    from ratchet.judge.schemas import CompareFactOut

    evidence = "Tavily answer: The EUR to USD rate today is 1.1196."
    changed = CompareFactOut(changed=True, new_value="1.1196", confidence=0.9)
    assert _grounded_compare(changed, "1.0800", evidence) == changed
    invented = CompareFactOut(changed=True, new_value="1.2500", confidence=0.9)
    out = _grounded_compare(invented, "1.0800", evidence)
    assert out is not None and not out.changed and out.confidence <= 0.3
    same = CompareFactOut(changed=True, new_value="1.08", confidence=0.9)  # not a change
    out = _grounded_compare(same, "1.0800", "rate 1.08")
    assert out is not None and not out.changed
    blind = CompareFactOut(changed=False, new_value=None, confidence=0.95)
    out = _grounded_compare(blind, "1.0800", "The sources do not state a rate.")
    assert out is not None and out.confidence <= 0.3


async def test_unsure_nano_comparison_gets_a_super_second_opinion(tmp_path: Path) -> None:
    pair = factory.by_name("d1_fx_drift")
    client = (
        FakeModelClient()
        .add("compare_fact", '{"changed": false, "new_value": null, "confidence": 0.4}')
        .add("compare_fact", '{"changed": true, "new_value": "0.95", "confidence": 0.9}')
        .add("attribute_drift", '{"attributable": true, "confidence": 0.9, "reason": "rate"}')
    )
    with settings(tmp_path):
        out = await drift.check_pair(
            _pair_verdict("uncertain"), pair.golden, pair.replay, client, FixtureTavily(TAVILY_DIR)
        )
    compare_tiers = [c.tier for c in client.calls_for("compare_fact")]
    assert compare_tiers == ["nano", "super"]
    assert out.outcome == "drift" and out.drift[0].new_value == "0.95"


def test_replay_value_reads_the_new_value_off_the_replays_own_tool_call() -> None:
    pair = factory.by_name("d1_fx_drift")  # finance.rate 0.92 → 0.95, same args
    fact = drift.observed_facts(pair.golden, pair.replay)[0]
    assert drift.replay_value(fact, pair.golden, pair.replay) == "0.95"
    assert drift.replay_value(fact, pair.golden, pair.golden) is None  # unchanged


@respx.mock
async def test_live_tavily_stops_at_the_daily_search_cap(tmp_path: Path) -> None:
    """Past ``daily_cap`` live searches in a UTC day, a cache miss is ``drift_unavailable``
    instead of another paid call; cache hits still answer."""
    from ratchet.errors import UpstreamError

    payload = json.loads((TAVILY_DIR / "token_factory_pricing.json").read_text())
    route = respx.post(SEARCH).mock(return_value=httpx.Response(200, json=payload))
    with settings(tmp_path):
        store = Store.open(tmp_path / "data")
        live = LiveTavily("tvly-test", store, daily_cap=2)
        await live.search("first")
        await live.search("second")
        await live.search("first")  # a hit: free
        with pytest.raises(UpstreamError) as info:
            await live.search("third")
        assert info.value.code == "drift_unavailable"
        assert await live.search("second")  # still served from the cache
    assert route.call_count == 2


def test_a_drift_check_keeps_only_http_source_links() -> None:
    from ratchet.models import DriftCheck

    base = {
        "fact_id": "f1",
        "query": "EUR to USD",
        "tavily_results_ref": "tavily/x.json",
        "changed": True,
        "old_value": "1.08",
        "new_value": "1.12",
        "confidence": 0.9,
    }
    assert DriftCheck(**base, source_url="https://example.com/x").source_url is not None
    assert DriftCheck(**base, source_url="javascript:alert(1)").source_url is None
    assert DriftCheck(**base, source_url="data:text/html,hi").source_url is None
