"""Cost estimates printed before any bench spend (budget guard, $12 bench cap).

Token figures are the per-call budgets; escalation rates are the sweep plan's
(per pair: 1 Nano screen, ~0.30 Super checks, ~0.10 Ultra adjudications). When the bench store
already holds recorded goldens, the per-replay agent cost is the *observed* mean agent spend of
those goldens (a replay re-runs the same task), else the budgeted 25k in / 5k out on Super.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ratchet.config.pricing import cost_usd
from ratchet.models import Tier
from ratchet.store import Store

__all__ = [
    "Line",
    "Estimate",
    "per_replay_usd",
    "judge_usd_per_pair",
    "sweep_estimate",
    "build_estimate",
    "TOKENS",
    "ESCALATION",
]

TOKENS: dict[str, tuple[Tier, int, int]] = {
    "agent_replay": ("super", 25_000, 5_000),  # replay budget
    "screen": ("nano", 8_000, 300),  # screen budget
    "check": ("super", 12_000, 1_000),  # check budget
    "adjudicate": ("ultra", 15_000, 2_000),  # adjudicate budget (+ reasoning within the output cap)
    "narrative": ("ultra", 6_000, 400),
    "compare_fact": ("nano", 4_000, 200),
    "attribute_drift": ("super", 8_000, 300),
    "bless_extract": ("nano", 12_000, 800),
    "bless_criteria": ("nano", 6_000, 300),
    "bless_side_effect": ("nano", 1_000, 120),
    "inject": ("ultra", 6_000, 1_500),  # injection budget per variant
}
ESCALATION: dict[str, float] = {"super": 0.30, "ultra": 0.10}
DRIFT_PAIR_SHARE = 0.25  # share of pairs that reach the drift check (world-dependent, flagged)


def _usd(name: str, times: float = 1.0) -> float:
    tier, prompt, completion = TOKENS[name]
    return cost_usd(tier, prompt, completion) * times


@dataclass
class Line:
    item: str
    count: float
    usd: float


@dataclass
class Estimate:
    title: str
    lines: list[Line] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, item: str, count: float, unit_usd: float) -> None:
        self.lines.append(Line(item, count, count * unit_usd))

    @property
    def total(self) -> float:
        return sum(line.usd for line in self.lines)

    def render(self) -> str:
        out = [self.title]
        for line in self.lines:
            out.append(f"  {line.item:<44} {line.count:>8.0f}  ${line.usd:>8.2f}")
        out.append(f"  {'total':<44} {'':>8}  ${self.total:>8.2f}")
        out += [f"  note: {n}" for n in self.notes]
        return "\n".join(out)


def per_replay_usd(store: Store, run_ids: list[str]) -> tuple[float, str]:
    """Mean agent spend of the given (golden) runs, or the replay budget."""
    if run_ids:
        marks = ",".join("?" for _ in run_ids)
        rows = store.query(
            f"SELECT run_id, SUM(usd) AS usd FROM costs WHERE purpose = 'agent' "
            f"AND run_id IN ({marks}) GROUP BY run_id",
            tuple(run_ids),
        )
        values = [float(r["usd"] or 0.0) for r in rows]
        if values and sum(values) > 0:
            return sum(values) / len(values), f"observed mean of {len(values)} recorded goldens"
    return _usd("agent_replay"), "replay budget (25k in / 5k out on super)"


def judge_usd_per_pair(mode: str, *, drift: bool = True) -> float:
    if mode == "nano_only":
        base = _usd("screen")
    elif mode == "super_only":
        base = _usd("check")
    elif mode == "ultra_only":
        base = _usd("adjudicate")
    else:
        base = (
            _usd("screen")
            + _usd("check", ESCALATION["super"])
            + _usd("adjudicate", ESCALATION["ultra"])
        )
    if drift:
        base += DRIFT_PAIR_SHARE * (_usd("compare_fact") + _usd("attribute_drift"))
    return base


def sweep_estimate(
    *,
    name: str,
    n_edits: int,
    pairs_per_edit: float,
    mode: str,
    drift: bool,
    replay_usd: float,
    replay_source: str,
    reuse_replays: bool,
) -> Estimate:
    est = Estimate(
        f"Estimated cost of `{name}` ({n_edits} edits, ~{pairs_per_edit:.1f} pairs each)"
    )
    pairs = n_edits * pairs_per_edit
    if reuse_replays:
        est.notes.append("replays reused from the primary run: no agent spend")
    else:
        est.add("agent replays (super)", pairs, replay_usd)
        est.notes.append(f"per-replay cost: {replay_source}")
    est.add(f"judging ({mode})", pairs, judge_usd_per_pair(mode, drift=drift))
    est.add("narratives (ultra)", n_edits, _usd("narrative"))
    return est


def build_estimate(
    *,
    n_goldens: int,
    n_inject_variants: int,
    replays_per_variant: float,
    n_d1: int,
    replay_usd: float,
    tries: float = 1.3,
) -> Estimate:
    est = Estimate("Estimated cost of dataset construction")
    if n_goldens:
        est.add("golden recordings (agent, super)", n_goldens, replay_usd)
        bless = _usd("bless_extract") + _usd("bless_criteria") + _usd("bless_side_effect")
        est.add("bless enrichment (nano)", n_goldens, bless)
    if n_inject_variants:
        est.add("inject_regression calls (ultra)", n_inject_variants * tries, _usd("inject"))
        est.add(
            "manifestation replays (agent, super)",
            n_inject_variants * tries * replays_per_variant,
            replay_usd,
        )
    if n_d1:
        est.add("D1 manifestation replays (agent, super)", n_d1 * replays_per_variant, replay_usd)
    est.notes.append(f"~{tries} injection tries per variant (max 3)")
    return est
