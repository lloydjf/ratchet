"""Scenario 3 on live Tavily: the overlay world uses today's real rate.

A scenario's ``live_world`` block (``scenarios/<k>/scenario.yaml``) names a world-fixture
value that should track the real world when the drift check runs against live Tavily::

    live_world:
      tool: finance.rate        # the world-dependent tool the value feeds
      file: fx.json             # the override file in the scenario's world/
      base: EUR
      quote: USD
      date: "2026-10-14"        # the goldens' frozen date
      query: EUR to USD exchange rate today

With ``ratchet_tavily_mode == "live"`` and a key, the value is read from Tavily's answer once
per UTC day (``LiveTavily`` caches by query + day in ``tavily_cache``): the first ``d.dddd``
number with 0.5 < rate < 2.0, rounded to 4 decimals. Otherwise, or when Tavily fails or the
answer has no usable number, the fixture value stays and the scenario's recorded Tavily
fixtures are used for the drift check too, so the evidence always matches the overlay.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ratchet.config.settings import Settings
from ratchet.errors import RatchetError
from ratchet.judge.tavily import LiveTavily, SearchParams, TavilySource
from ratchet.log import get_logger
from ratchet.store import Store

__all__ = ["LiveWorldSpec", "WorldChoice", "parse_rate", "live_rate", "patched_override", "choose"]

_RATE = re.compile(r"(?<![\d.])(\d\.\d{2,6})(?![\d])")
_log = get_logger("simulate")


class LiveWorldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "finance.rate"
    file: str = "fx.json"
    base: str
    quote: str
    date: str
    query: str


@dataclass(frozen=True)
class WorldChoice:
    override: Path  # world-fixture overrides to lay over the base world
    overlay_name: str  # data/overlays/<name>
    tavily_dir: Path | None  # scenario fixtures, or None for the configured source
    fixtures_only: bool  # pin the drift check to tavily_dir even in live mode
    live_rate: float | None = None


def parse_rate(text: str | None) -> float | None:
    """The first plausible exchange rate in ``text`` (0.5 < rate < 2.0), 4 decimals."""
    for match in _RATE.finditer(text or ""):
        value = float(match.group(1))
        if 0.5 < value < 2.0:
            return round(value, 4)
    return None


async def live_rate(spec: LiveWorldSpec, tavily: TavilySource) -> float | None:
    try:
        results = await tavily.search(
            spec.query, SearchParams(search_depth="advanced", topic="general", time_range=None)
        )
    except RatchetError as exc:
        _log.warning("simulate.live_rate.unavailable", code=exc.code)
        return None
    rate = parse_rate(getattr(results, "answer", None))
    if rate is None:
        _log.warning("simulate.live_rate.unparsed")
    return rate


def patched_override(override: Path, spec: LiveWorldSpec, rate: float, dest: Path) -> Path:
    """A copy of ``override`` whose ``spec.file`` holds ``rate`` for base/quote on date."""
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(override, dest)
    path = dest / spec.file
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("rates", {}).setdefault(f"{spec.base}/{spec.quote}", {})[spec.date] = rate
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


async def choose(
    settings: Settings,
    store: Store,
    scenario_id: str,
    override: Path,
    tavily_dir: Path | None,
    spec: LiveWorldSpec | None,
    *,
    tavily: TavilySource | None = None,
) -> WorldChoice:
    """The world overrides and Tavily source a scenario run should use (module docstring)."""
    fixed = WorldChoice(override, scenario_id, tavily_dir, fixtures_only=tavily_dir is not None)
    key = settings.tavily_api_key
    if spec is None or settings.ratchet_tavily_mode != "live" or (key is None and tavily is None):
        return fixed
    source = tavily or LiveTavily(key.get_secret_value() if key else "", store)
    rate = await live_rate(spec, source)
    if rate is None:
        return fixed
    name = f"{scenario_id}-live-{rate:.4f}"
    dest = store.data_dir / "overlays" / f"{name}-world"
    patched = patched_override(override, spec, rate, dest)
    _log.info("simulate.live_rate", scenario=scenario_id, rate=rate)
    return WorldChoice(patched, name, None, fixtures_only=False, live_rate=rate)
