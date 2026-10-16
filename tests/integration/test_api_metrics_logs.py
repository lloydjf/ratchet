"""Product metrics + demo funnel routes, edit logs, and the /metrics series."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_factories import NOW, seed_decided, seed_running  # noqa: E402
from test_api_fakes import api_reset, make_env  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.dashboard_api.views import DigestView, MetricRow  # noqa: E402
from ratchet.models import ReplayBatch  # noqa: E402
from ratchet.orchestrator import prom  # noqa: E402
from ratchet.orchestrator.metrics import FUNNEL_STEPS  # noqa: E402

__all__ = ["api_reset"]
V1 = "/api/v1"


def _batch(edit_id: str, batch_id: str) -> ReplayBatch:
    return ReplayBatch(
        batch_id=batch_id,
        edit_id=edit_id,
        golden_run_ids=["run_g1"],
        skills_tar_ref="skills.tar.zst",
        cassettes_ref="cassettes.tar.zst",
        worker_image="ratchet-worker:dev",
        agent_tier="super",
        sandbox="process",
        timeout_s=600,
        candidate_commit="abc",
    )


async def test_product_metrics_route_returns_the_cli_rows(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "p1", at=clock.now())
    async with env.client() as client:
        res = await client.get(f"{V1}/metrics/product", params={"since": "7d"})
        default = await client.get(f"{V1}/metrics/product")
        bad = await client.get(f"{V1}/metrics/product", params={"since": "seven"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"since", "rows"}
    rows = {r["metric"]: r["value"] for r in body["rows"]}
    assert rows["Protected edits"] == "1"
    assert rows["Decision mix"].startswith("PASS 0% (0/1)")
    assert default.json()["rows"] == body["rows"]
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_request"


async def test_demo_funnel_route_is_operator_only(tmp_path: Path) -> None:
    env = make_env(tmp_path / "op")
    env.store.emit("demo.landing_view", "-", {}, table="demo_events", session_hash="s1")
    async with env.client() as client:
        res = await client.get(f"{V1}/metrics/demo", params={"since": "1d"})
    assert [r["metric"] for r in res.json()["rows"]] == list(FUNNEL_STEPS)
    assert res.json()["rows"][0]["value"] == "1"
    public = make_env(tmp_path / "pub", public=True)
    async with public.client() as client:
        refused = await client.get(f"{V1}/metrics/demo")
        product = await client.get(f"{V1}/metrics/product")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_public"
    assert product.status_code == 200


async def test_digest_view_carries_the_metrics_table(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.backend.digest = DigestView(
        digest_id="digest_1",
        period_start=NOW,
        period_end=NOW,
        narrative="A quiet week.",
        verdict_ids=[],
        merged=[],
        blocked=[],
        metrics=[MetricRow(metric="Blessed traces", value="3")],
    )
    async with env.client() as client:
        body = (await client.get(f"{V1}/digest/latest")).json()
    assert body["metrics"] == [{"metric": "Blessed traces", "value": "3"}]


async def test_edit_logs_tail_worker_logs_and_crash_records(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    edit = seed_running(env.store, "l1")
    batch_id = "batch_l1_a1_0"
    env.store.save_batch_spec(_batch(edit.edit_id, batch_id), "local")
    log = env.store.batch_dir(batch_id) / "worker.log"
    log.write_text("".join(f"line {i}\n" for i in range(600)), encoding="utf-8")
    crash = env.store.edit_dir(edit.edit_id) / "attempt_1" / "crash.json"
    crash.parent.mkdir(parents=True, exist_ok=True)
    info = {"state": "judging", "error_type": "KeyError", "message": "x", "frames": ["f.py:1"]}
    crash.write_text(json.dumps(info), encoding="utf-8")
    async with env.client() as client:
        res = await client.get(f"{V1}/edits/{edit.edit_id}/logs")
        missing = await client.get(f"{V1}/edits/edit_nope/logs")
    body = res.json()
    assert res.status_code == 200 and body["truncated"] is True
    lines = body["lines"]
    assert lines[0] == "line 100" and "line 599" in lines
    assert lines[-2] == "--- crash in judging (attempt_1): KeyError: x"
    assert lines[-1] == "    f.py:1"
    assert len(lines) == 502
    assert missing.status_code == 404


async def test_edit_logs_are_hidden_on_the_public_host(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    edit = seed_running(env.store, "l2")
    async with env.client() as client:
        res = await client.get(f"{V1}/edits/{edit.edit_id}/logs")
    assert res.status_code == 403 and res.json()["error"]["code"] == "not_public"


async def test_metrics_expose_stalls_simulate_failures_and_orchestrator_series(
    tmp_path: Path,
) -> None:
    prom.reset()
    env = make_env(tmp_path)
    edit = seed_running(env.store, "s1")
    env.store.save_batch_spec(_batch(edit.edit_id, "batch_s1_a1_0"), "local")
    env.store.update_batch("batch_s1_a1_0", status="stalled")
    env.store.emit("batch.stalled", edit.edit_id, {"batch_id": "batch_s1_a1_0"})
    for status in ("done", "failed", "done", "failed"):
        env.store.emit("simulate.finished", "x", {"scenario": "1", "status": status})
    prom.job_submitted("batch_s1_a1_0", "local")
    prom.job_heartbeat("batch_s1_a1_0")
    prom.job_finished("batch_s1_a1_0", "local", "ok")
    async with env.client() as client:
        text = (await client.get("/metrics")).text
    assert "ratchet_batches_stalled 1.0" in text
    assert "ratchet_batch_stalls_total 1.0" in text
    assert "ratchet_simulate_recent_failed 2.0" in text
    assert "ratchet_simulate_recent_runs 4.0" in text
    assert 'ratchet_jobs_total{runner="local",status="ok"} 1.0' in text
    assert 'ratchet_job_cold_start_s_count{runner="local"} 1.0' in text
    assert "# TYPE ratchet_edit_duration_s histogram" in text
    assert "# TYPE ratchet_stage_duration_ms histogram" in text
    assert "ratchet_model_usd_total" not in text  # lives on the proxy registry (:8081)
    prom.reset()


async def test_costs_savings_line_covers_every_verdict_in_the_window(tmp_path: Path) -> None:
    """A fresh public session sees the host's savings over the window, not $0.00 from "the
    last edit it can see"; ``last_edit`` stays per caller."""
    from datetime import timedelta

    import pytest

    from ratchet.store import CostRow

    env = make_env(tmp_path, public=True)
    now = clock.now()
    seed_decided(env.store, "w1", session_hash="a" * 16, at=now)
    seed_decided(env.store, "w2", session_hash="b" * 16, at=now)
    seed_decided(env.store, "w3", session_hash="c" * 16, at=now - timedelta(days=30))  # older
    rows = [
        ("k1", "edit_w1", "screen", "nano", 1000, 100, 0.0002),
        ("k2", "edit_w2", "check", "super", 2000, 200, 0.0012),
        ("k3", "edit_w2", "agent", "super", 5000, 500, 0.0030),  # replay spend: not judging
        ("k4", "edit_w3", "check", "super", 9000, 900, 0.0050),
    ]
    for call_id, edit_id, purpose, tier, pt, ct, usd in rows:
        env.store.record_cost(
            CostRow(call_id, "judge", edit_id, tier, "m", pt, ct, usd, purpose)  # type: ignore[arg-type]
        )
    async with env.client() as client:
        week = (await client.get(f"{V1}/costs")).json()
        month = (
            await client.get(
                f"{V1}/costs",
                params={
                    "from": (now - timedelta(days=40)).isoformat(),
                    "to": (now + timedelta(minutes=1)).isoformat(),
                },
            )
        ).json()
    line = week["counterfactual"]
    assert week["last_edit"] is None  # nothing of this session's own
    assert line["n_verdicts"] == 2
    assert line["actual_usd"] == pytest.approx(0.0014)
    assert line["actual_usd_by_tier"] == {"nano": pytest.approx(0.0002), "super": 0.0012}
    assert line["all_ultra_usd"] == pytest.approx((3000 * 1.0 + 300 * 3.0) / 1e6)
    assert 0 < line["saved_ratio"] < 1
    assert month["counterfactual"]["n_verdicts"] == 3
    assert month["counterfactual"]["actual_usd"] == pytest.approx(0.0064)
