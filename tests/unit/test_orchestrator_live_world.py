"""Scenario 3 on live Tavily: the overlay follows today's real rate (fake Tavily here)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import SCENARIOS, make_settings, open_store, seeded_repo  # noqa: E402

from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.judge.tavily import FixtureTavily, SearchParams, TavilyResults  # noqa: E402
from ratchet.orchestrator.context import load_options, tavily_source  # noqa: E402
from ratchet.orchestrator.live_world import choose, parse_rate  # noqa: E402
from ratchet.orchestrator.scenarios import get_scenario  # noqa: E402
from ratchet.orchestrator.simulate import SimulateService  # noqa: E402

ANSWER = "The EUR to USD exchange rate today is 1.1212 - 1.1223, per several sources."


class FakeTavily:
    def __init__(self, answer: str | None = ANSWER, fail: bool = False) -> None:
        self.answer, self.fail, self.queries = answer, fail, []

    async def search(self, query: str, params: SearchParams | None = None) -> TavilyResults:
        self.queries.append(query)
        if self.fail:
            raise UpstreamError("down", code="drift_unavailable")
        return TavilyResults(query=query, hits=[], ref="fake", answer=self.answer)

    async def extract(self, url: str) -> str:
        return ""


@pytest.mark.parametrize(
    ("text", "rate"),
    [(ANSWER, 1.1212), ("1 EUR = 1.16304 USD", 1.163), ("rates: 145.2 or 7.5", None), (None, None)],
)
def test_parse_rate_takes_the_first_plausible_rate(text: str | None, rate: float | None) -> None:
    assert parse_rate(text) == rate


def test_scenario_three_declares_its_live_world() -> None:
    spec = get_scenario(SCENARIOS, "3").live_world
    assert spec is not None and (spec.base, spec.quote, spec.tool) == ("EUR", "USD", "finance.rate")
    assert get_scenario(SCENARIOS, "1").live_world is None


async def test_live_mode_patches_the_overlay_with_todays_rate(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, ratchet_tavily_mode="live", tavily_api_key="tvly-fake")
    store = open_store(settings)
    scenario = get_scenario(SCENARIOS, "3")
    fake = FakeTavily()
    assert scenario.world_dir is not None
    choice = await choose(
        settings,
        store,
        "3",
        scenario.world_dir,
        scenario.tavily_dir,
        scenario.live_world,
        tavily=fake,
    )
    assert fake.queries == ["EUR to USD exchange rate today"]
    assert choice.live_rate == 1.1212 and not choice.fixtures_only and choice.tavily_dir is None
    fx = json.loads((choice.override / "fx.json").read_text())
    assert fx["rates"]["EUR/USD"]["2026-10-14"] == 1.1212
    assert (
        json.loads((scenario.world_dir / "fx.json").read_text())["rates"]["EUR/USD"]["2026-10-14"]
        == 1.163
    )  # the fixture itself is untouched


@pytest.mark.parametrize("fake", [FakeTavily(fail=True), FakeTavily(answer="no numbers")])
async def test_tavily_trouble_falls_back_to_the_fixtures_for_both(
    tmp_path: Path, fake: FakeTavily
) -> None:
    settings = make_settings(tmp_path, ratchet_tavily_mode="live", tavily_api_key="tvly-fake")
    store = open_store(settings)
    scenario = get_scenario(SCENARIOS, "3")
    assert scenario.world_dir is not None
    choice = await choose(
        settings,
        store,
        "3",
        scenario.world_dir,
        scenario.tavily_dir,
        scenario.live_world,
        tavily=fake,
    )
    assert choice.override == scenario.world_dir and choice.live_rate is None
    assert choice.fixtures_only and choice.tavily_dir == scenario.tavily_dir


async def test_fixtures_mode_never_asks_tavily(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    scenario = get_scenario(SCENARIOS, "3")
    fake = FakeTavily()
    assert scenario.world_dir is not None
    choice = await choose(
        settings,
        open_store(settings),
        "3",
        scenario.world_dir,
        scenario.tavily_dir,
        scenario.live_world,
        tavily=fake,
    )
    assert fake.queries == [] and choice.override == scenario.world_dir


async def test_a_fallback_run_pins_the_drift_check_to_the_fixtures(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, ratchet_tavily_mode="live", tavily_api_key="tvly-fake")
    store = open_store(settings)
    seeded_repo(settings)

    class Fake:
        def submit_edit(self, edit: object, diff: str) -> None:
            store.save_edit(edit, diff)  # type: ignore[arg-type]
            self.edit = edit

        def add_listener(self, listener: object) -> None:
            pass

    fake = Fake()
    service = SimulateService(settings, store, fake)  # type: ignore[arg-type]
    service.live_tavily = FakeTavily(fail=True)
    start = await service.start("3", "a" * 16)
    options = load_options(store, str(start.edit_id))
    assert options.tavily_fixtures_only and options.world_ref
    assert isinstance(tavily_source(settings, store, options), FixtureTavily)
