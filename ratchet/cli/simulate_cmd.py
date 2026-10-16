"""``ratchet simulate <k> [--headless] [--record-fixture] [--repeat N]`` (deploy smoke).

Runs the scenario in-process through the same ``SimulateService`` the dashboard uses,
prints each stage as it changes, then the verdict headline. Exit code 0 iff the decision
matches the scenario's ``expected_decision``.

``RATCHET_SIMULATE_ENABLED`` gates only the HTTP route: this command runs on the
operator's own machine, so it works whichever way the flag is set.

``--repeat N`` (to check a scenario reaches its decision every time) runs the scenario
N times, sequentially, each in a fresh session, and reports ``k/N matched`` with the mean
and p95 duration; exit 0 only if every run matched.
"""

from __future__ import annotations

import asyncio
import math
import secrets
import statistics
from dataclasses import dataclass

import typer

from ratchet.config.settings import Settings
from ratchet.models import Verdict
from ratchet.orchestrator.progress import progress_for
from ratchet.orchestrator.recorded import record_fixture
from ratchet.orchestrator.service import Orchestrator
from ratchet.orchestrator.simulate import SimulateService
from ratchet.store import Store

__all__ = [
    "SimulateOutcome",
    "RepeatOutcome",
    "simulate_headless",
    "simulate_repeat",
    "print_outcome",
    "print_repeat",
    "cli_settings",
]

_TICK_S = 0.2


@dataclass(frozen=True)
class SimulateOutcome:
    verdict: Verdict
    expected: str
    elapsed_s: float

    @property
    def matched(self) -> bool:
        return self.verdict.decision == self.expected


@dataclass(frozen=True)
class RepeatOutcome:
    scenario_id: str
    outcomes: tuple[SimulateOutcome, ...]

    @property
    def n_matched(self) -> int:
        return sum(o.matched for o in self.outcomes)

    @property
    def all_matched(self) -> bool:
        return bool(self.outcomes) and self.n_matched == len(self.outcomes)

    @property
    def mean_s(self) -> float:
        return round(statistics.fmean(o.elapsed_s for o in self.outcomes), 1)

    @property
    def p95_s(self) -> float:
        """Nearest-rank p95 of the run durations."""
        ordered = sorted(o.elapsed_s for o in self.outcomes)
        return ordered[max(math.ceil(0.95 * len(ordered)) - 1, 0)]


def cli_settings(settings: Settings) -> Settings:
    """The CLI runs Simulate on the operator's own machine whatever the HTTP flag says."""
    if settings.ratchet_simulate_enabled:
        return settings
    return settings.model_copy(update={"ratchet_simulate_enabled": True})


async def _follow(store: Store, edit_id: str, started: float, echo: bool) -> None:
    last = ""
    loop = asyncio.get_running_loop()
    while True:
        progress = await asyncio.to_thread(progress_for, store, edit_id)
        if progress is None:
            return
        if echo and progress.stage_label != last:
            typer.echo(f"  {loop.time() - started:5.1f}s  {progress.stage_label}")
            last = progress.stage_label
        await asyncio.sleep(_TICK_S)


async def simulate_headless(
    settings: Settings,
    store: Store,
    scenario_id: str,
    *,
    record: bool = False,
    echo: bool = True,
    timeout_s: float = 300.0,
) -> SimulateOutcome:
    settings = cli_settings(settings)
    orchestrator = Orchestrator(settings, store, poll_s=0.25, digest_enabled=False)
    await orchestrator.start(watch=False)
    try:
        service = SimulateService(settings, store, orchestrator)
        info = next((s for s in service.scenarios() if s.scenario_id == scenario_id), None)
        started = asyncio.get_running_loop().time()
        session = secrets.token_hex(8)
        start = await service.start(scenario_id, session)
        if start.edit_id is None or start.verdict_id is None:
            raise RuntimeError("the scenario was queued; try again when a slot is free")
        if echo:
            typer.echo(f"Simulate {scenario_id}: {info.title if info else ''} ({start.verdict_id})")
        async with asyncio.timeout(timeout_s):
            await _follow(store, start.edit_id, started, echo)
            await orchestrator.wait_idle(timeout_s)
        verdict = await asyncio.to_thread(store.load_verdict, start.verdict_id)
        if record:
            path = await asyncio.to_thread(
                record_fixture, store, settings, verdict.verdict_id, scenario_id
            )
            if echo:
                typer.echo(f"Recorded run written to {path}")
    finally:
        await orchestrator.stop()
    elapsed = asyncio.get_running_loop().time() - started
    expected = info.expected_decision if info else verdict.decision
    return SimulateOutcome(verdict, expected, round(elapsed, 1))


def print_outcome(outcome: SimulateOutcome, settings: Settings) -> None:
    v = outcome.verdict
    badge = " [Simulated models]" if v.simulated_models else ""
    typer.echo(f"{v.decision} ({v.action}) in {outcome.elapsed_s:.1f}s{badge}")
    typer.echo(v.headline)
    mark = "matches" if outcome.matched else "DOES NOT match"
    typer.echo(f"Expected {outcome.expected}: {mark}.")
    typer.echo(f"{settings.ratchet_public_url.rstrip('/')}/verdicts/{v.verdict_id}")


async def simulate_repeat(
    settings: Settings,
    store: Store,
    scenario_id: str,
    *,
    repeat: int,
    echo: bool = True,
    timeout_s: float = 300.0,
) -> RepeatOutcome:
    """Run ``scenario_id`` ``repeat`` times, one after another, each in a fresh session."""
    outcomes: list[SimulateOutcome] = []
    for index in range(repeat):
        outcome = await simulate_headless(
            settings, store, scenario_id, echo=False, timeout_s=timeout_s
        )
        outcomes.append(outcome)
        if echo:
            mark = "match" if outcome.matched else "MISMATCH"
            typer.echo(
                f"  run {index + 1}/{repeat}: {outcome.verdict.decision} "
                f"in {outcome.elapsed_s:.1f}s ({mark})"
            )
    return RepeatOutcome(scenario_id, tuple(outcomes))


def print_repeat(result: RepeatOutcome) -> None:
    n = len(result.outcomes)
    expected = result.outcomes[0].expected if result.outcomes else "?"
    typer.echo(
        f"Scenario {result.scenario_id}: {result.n_matched}/{n} matched (expected {expected}); "
        f"mean {result.mean_s:.1f}s, p95 {result.p95_s:.1f}s"
    )
