"""Per-verdict cost summary.

Totals come from the proxy's ``costs`` rows for the edit (``Store.spend(edit_id=...)``):

- ``usd_by_tier`` / ``tokens_by_tier``: everything spent on the edit (agent replays + judge);
- ``judge_usd_by_tier``: purposes screen, check, adjudicate, extract, narrative;
- ``agent_usd_by_tier``: purpose ``agent`` (the replays themselves);
- ``counterfactual_ultra_usd``: the judge tokens priced as if every judge call ran on Ultra
  (``all_ultra_usd = Σ tokens × ultra prices``);
- ``saved_ratio = 1 - judge_usd / counterfactual_ultra_usd`` (0 when nothing was judged).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ratchet.config.pricing import counterfactual_ultra_usd
from ratchet.models import TIERS, Tier
from ratchet.store.ledger import TierTotals

__all__ = ["JUDGE_PURPOSES", "CostSummary", "SpendSource", "verdict_costs"]

JUDGE_PURPOSES: tuple[str, ...] = ("screen", "check", "adjudicate", "extract", "narrative")


class SpendSource(Protocol):
    def spend(
        self, *, edit_id: str | None = None, purposes: list[str] | None = None
    ) -> TierTotals: ...


@dataclass(frozen=True)
class CostSummary:
    usd_by_tier: dict[Tier, float]
    tokens_by_tier: dict[Tier, int]
    judge_usd_by_tier: dict[Tier, float]
    counterfactual_ultra_usd: float
    saved_ratio: float
    agent_usd_by_tier: dict[Tier, float] = field(default_factory=dict)
    judge_tokens_by_tier: dict[Tier, int] = field(default_factory=dict)

    @property
    def total_usd(self) -> float:
        return sum(self.usd_by_tier.values())

    @property
    def judge_usd(self) -> float:
        return sum(self.judge_usd_by_tier.values())


def _tokens(totals: TierTotals) -> dict[Tier, int]:
    return {t: totals.prompt_tokens[t] + totals.completion_tokens[t] for t in TIERS}


def verdict_costs(store: SpendSource, edit_id: str) -> CostSummary:
    everything = store.spend(edit_id=edit_id)
    judge = store.spend(edit_id=edit_id, purposes=list(JUDGE_PURPOSES))
    agent = store.spend(edit_id=edit_id, purposes=["agent"])
    counterfactual = counterfactual_ultra_usd(
        sum(judge.prompt_tokens.values()), sum(judge.completion_tokens.values())
    )
    judge_usd = sum(judge.usd.values())
    saved = 1.0 - judge_usd / counterfactual if counterfactual > 0 else 0.0
    return CostSummary(
        usd_by_tier=dict(everything.usd),
        tokens_by_tier=_tokens(everything),
        judge_usd_by_tier=dict(judge.usd),
        counterfactual_ultra_usd=counterfactual,
        saved_ratio=max(0.0, min(1.0, saved)),
        agent_usd_by_tier=dict(agent.usd),
        judge_tokens_by_tier=_tokens(judge),
    )
