"""Pricing and cost: per-verdict costs by tier match hand-calculated USD.

Also covers judge vs agent spend and the all-Ultra counterfactual. The proxy
side of the cost check (6 usage blocks) is ``tests/unit/test_proxy_cost.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import settings  # noqa: E402

from ratchet.config.pricing import cost_usd  # noqa: E402
from ratchet.judge.costs import verdict_costs  # noqa: E402
from ratchet.store import CostRow, Store  # noqa: E402

ROWS = [
    ("c1", "super", 25_000, 5_000, "agent"),
    ("c2", "nano", 8_000, 500, "screen"),
    ("c3", "nano", 8_000, 500, "screen"),
    ("c4", "super", 12_000, 1_000, "check"),
    ("c5", "ultra", 15_000, 2_000, "adjudicate"),
    ("c6", "ultra", 3_000, 400, "narrative"),
    ("c7", "nano", 1_000, 100, "extract"),
]


def test_verdict_costs_match_hand_calculation(tmp_path: Path) -> None:
    """Computed USD per tier matches the hand calculation."""
    with settings(tmp_path):
        store = Store.open(tmp_path / "data")
        for call_id, tier, pt, ct, purpose in ROWS:
            store.record_cost(
                CostRow(call_id, "judge_e1", "e1", tier, f"m-{tier}", pt, ct,
                        cost_usd(tier, pt, ct), purpose)
            )  # fmt: skip
        store.record_cost(CostRow("x1", "other", "e2", "ultra", "m", 9_999, 9_999, 1.0, "check"))
        summary = verdict_costs(store, "e1")
    # nano: 17k in / 1.1k out; super judge 12k/1k + agent 25k/5k; ultra 18k/2.4k
    assert summary.judge_usd_by_tier["nano"] == pytest.approx((17_000 * 0.06 + 1_100 * 0.24) / 1e6)
    assert summary.judge_usd_by_tier["super"] == pytest.approx((12_000 * 0.30 + 1_000 * 0.90) / 1e6)
    assert summary.judge_usd_by_tier["ultra"] == pytest.approx((18_000 * 1.0 + 2_400 * 3.0) / 1e6)
    assert summary.agent_usd_by_tier["super"] == pytest.approx((25_000 * 0.30 + 5_000 * 0.90) / 1e6)
    assert summary.usd_by_tier["super"] == pytest.approx(
        summary.judge_usd_by_tier["super"] + summary.agent_usd_by_tier["super"]
    )
    assert summary.tokens_by_tier["super"] == 25_000 + 5_000 + 12_000 + 1_000
    judge_in, judge_out = 17_000 + 12_000 + 18_000, 1_100 + 1_000 + 2_400
    assert summary.counterfactual_ultra_usd == pytest.approx(
        (judge_in * 1.0 + judge_out * 3.0) / 1e6
    )
    assert summary.saved_ratio == pytest.approx(
        1 - summary.judge_usd / summary.counterfactual_ultra_usd
    )
    assert 0 < summary.saved_ratio < 1 and summary.total_usd > summary.judge_usd


def test_no_judge_spend_means_no_savings(tmp_path: Path) -> None:
    with settings(tmp_path):
        summary = verdict_costs(Store.open(tmp_path / "data"), "nothing")
    assert summary.counterfactual_ultra_usd == 0 and summary.saved_ratio == 0
    assert summary.total_usd == 0
