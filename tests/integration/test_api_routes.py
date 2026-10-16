"""Dashboard API routes on the operator host (CONTRACTS §7; orchestrator-api-cli.md §8)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_factories import DIFF, NOW, later, make_trace, seed_decided, seed_running  # noqa: E402
from test_api_fakes import api_reset, make_env  # noqa: E402

from ratchet.dashboard_api.views import DigestView  # noqa: E402
from ratchet.store import CostRow  # noqa: E402

__all__ = ["api_reset"]
V1 = "/api/v1"


async def test_meta_reports_host_config_and_sets_session_cookie(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        res = await client.get(f"{V1}/meta")
    assert res.status_code == 200
    body = res.json()
    assert body["public_mode"] is False and body["action_token"] is None
    assert body["simulated_models"] is True and body["simulate_mode"] == "live"
    assert body["video_url"] is None and body["version"] == "0.1.0"
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("ratchet_session=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=86400" in cookie
    assert "Secure" not in cookie


async def test_meta_cookie_is_secure_when_public_url_is_https(tmp_path: Path) -> None:
    env = make_env(tmp_path, ratchet_public_url="https://demo.example")
    async with env.client() as client:
        res = await client.get(f"{V1}/meta")
    assert "Secure" in res.headers["set-cookie"]


async def test_api_alias_serves_the_same_handlers(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        assert (await client.get("/api/meta")).json() == (await client.get(f"{V1}/meta")).json()


async def test_scenarios_and_simulate_start_returns_preallocated_verdict(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        scenarios = (await client.get(f"{V1}/scenarios")).json()
        assert [s["scenario_id"] for s in scenarios] == ["1", "2", "3"]
        res = await client.post(f"{V1}/simulate/1")
        assert res.status_code == 200, res.text
        start = res.json()
        assert start["status"] == "running" and start["verdict_id"].startswith("verdict_")
        assert "ratchet_session=" in res.headers["set-cookie"]
        view = await client.get(f"{V1}/verdicts/{start['verdict_id']}")
    assert view.status_code == 200
    assert view.json()["verdict"] is None and view.json()["progress"]["k"] == 3
    assert env.simulate.started == [("1", start["session_hash"])]
    assert env.simulate.client_keys == ["127.0.0.1"]  # the IP bucket reaches the service


async def test_simulate_unknown_scenario_is_404_and_disabled_is_403(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        res = await client.post(f"{V1}/simulate/9")
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    off = make_env(tmp_path / "off", simulate_enabled=False)
    async with off.client() as client:
        res = await client.post(f"{V1}/simulate/1")
        assert (await client.get(f"{V1}/scenarios")).json() == []
    assert res.status_code == 403 and res.json()["error"]["code"] == "simulate_disabled"


async def test_verdict_list_is_paginated_newest_first_with_opaque_cursor(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    for i in range(5):
        seed_decided(env.store, f"a{i}", at=later(i))
    async with env.client() as client:
        first = (await client.get(f"{V1}/verdicts", params={"limit": 2})).json()
        assert [v["verdict_id"] for v in first["items"]] == ["verdict_a4", "verdict_a3"]
        second = (
            await client.get(f"{V1}/verdicts", params={"limit": 2, "cursor": first["next_cursor"]})
        ).json()
        third = (
            await client.get(f"{V1}/verdicts", params={"limit": 2, "cursor": second["next_cursor"]})
        ).json()
        bad = await client.get(f"{V1}/verdicts", params={"cursor": "%%%not-a-cursor"})
        too_big = await client.get(f"{V1}/verdicts", params={"limit": 201})
    assert [v["verdict_id"] for v in second["items"]] == ["verdict_a2", "verdict_a1"]
    assert [v["verdict_id"] for v in third["items"]] == ["verdict_a0"]
    assert third["next_cursor"] is None
    item = first["items"][0]
    assert item["n_pairs"] == 1 and item["outcome_counts"] == {"regression": 1}
    assert item["usd_total"] == pytest.approx(0.012)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_request"
    assert too_big.status_code == 422 and too_big.json()["error"]["code"] == "bad_request"


async def test_verdict_view_decided_has_no_progress_and_includes_diff(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "b1")
    async with env.client() as client:
        view = (await client.get(f"{V1}/verdicts/verdict_b1")).json()
        missing = await client.get(f"{V1}/verdicts/verdict_nope")
        odd = await client.get(f"{V1}/verdicts/..%2Fsecrets")
    assert view["state"] == "done" and view["progress"] is None
    assert view["verdict"]["decision"] == "BLOCK" and view["diff"] == DIFF
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert odd.status_code == 404


async def test_pair_view_loads_golden_and_replay_traces(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "c1")
    async with env.client() as client:
        pair = (await client.get(f"{V1}/verdicts/verdict_c1/pairs/run_gc1/run_rc1")).json()
        wrong = await client.get(f"{V1}/verdicts/verdict_c1/pairs/run_gc1/run_other")
    assert pair["decision"] == "BLOCK" and pair["pair"]["outcome"] == "regression"
    assert pair["golden"]["kind"] == "golden" and pair["replay"]["kind"] == "replay"
    assert wrong.status_code == 404
    assert pair["recorded"] is False


async def test_pair_view_says_when_the_verdict_is_a_recorded_run(tmp_path: Path) -> None:
    """PairView.recorded mirrors Verdict.recorded so the pair page shows the badge."""
    env = make_env(tmp_path)
    verdict = seed_decided(env.store, "rec1")
    env.store.save_verdict(verdict.model_copy(update={"recorded": True}))
    async with env.client() as client:
        pair = (await client.get(f"{V1}/verdicts/verdict_rec1/pairs/run_grec1/run_rrec1")).json()
    assert pair["recorded"] is True


async def test_rollback_and_approve_call_actions_as_operator(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "d1")
    async with env.client() as client:
        rolled = await client.post(f"{V1}/verdicts/verdict_d1/rollback")
        approved = await client.post(f"{V1}/verdicts/verdict_d1/approve")
    assert rolled.status_code == 200 and rolled.json()["verdict"]["action"] == "reverted"
    assert approved.json()["verdict"]["action"] == "merged"
    # toasts ride on the action responses only (the verdict was BLOCK → reverted)
    assert rolled.json()["toast"] == "Rolled back. main is unchanged."
    assert (
        approved.json()["toast"] == "Approved anyway. github-triage is re-applied and live on main."
    )
    assert env.backend.calls == [
        ("rollback", "verdict_d1", "operator"),
        ("approve", "verdict_d1", "operator"),
    ]


async def test_action_lock_conflict_maps_to_409(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "d2")
    env.store.acquire_action_lock("verdict_d2")

    async def locked(store: object, settings: object, verdict_id: str, *, actor: str) -> object:
        env.store.acquire_action_lock(verdict_id)  # raises ConflictError(action_locked)
        raise AssertionError("unreachable")

    env.backend.rollback = locked  # type: ignore[method-assign,assignment]
    async with env.client() as client:
        res = await client.post(f"{V1}/verdicts/verdict_d2/rollback")
    assert res.status_code == 409 and res.json()["error"]["code"] == "action_locked"


async def test_rebless_returns_new_and_archived_ids(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "e1")
    async with env.client() as client:
        res = await client.post(f"{V1}/pairs/run_ge1/run_re1/rebless")
    assert res.json() == {
        "new_golden_run_id": "run_re1_golden",
        "archived_run_id": "run_ge1",
        "toast": "Re-blessed. Future checks compare against today's result.",
    }
    assert env.backend.calls[-1] == ("rebless", "run_ge1", "run_re1", None)


async def test_edits_list_detail_and_replay(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "f1")
    seed_running(env.store, "f2")
    async with env.client() as client:
        page = (await client.get(f"{V1}/edits")).json()
        detail = (await client.get(f"{V1}/edits/edit_f2")).json()
        replay = await client.post(f"{V1}/edits/edit_f1/replay")
        missing = await client.post(f"{V1}/edits/edit_zz/replay")
    assert {e["edit"]["edit_id"] for e in page["items"]} == {"edit_f1", "edit_f2"}
    assert detail["state"] == "collecting" and detail["verdict_id"] == "verdict_f2"
    assert replay.status_code == 200 and replay.json()["state"] == "selecting"
    assert env.orchestrator.replayed == ["edit_f1"]
    assert missing.status_code == 404


async def test_edit_replay_without_orchestrator_is_503_not_ready(tmp_path: Path) -> None:
    env = make_env(tmp_path, with_orchestrator=False)
    seed_running(env.store, "f3")
    async with env.client() as client:
        res = await client.post(f"{V1}/edits/edit_f3/replay")
    assert res.status_code == 503 and res.json()["error"]["code"] == "not_ready"
    # The 503 has its own text, not the 409 "still acting on this verdict" action copy.
    error = res.json()["error"]
    assert error["message"] == "This host is not fully started yet; try again in a moment."
    assert error["details"]["reason"] == "The orchestrator is not running on this host."


async def test_traces_list_filters_and_trace_actions(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.store.save_trace(make_trace("run_a", label="Triage widgets", created_at=later(1)))
    env.store.save_trace(make_trace("run_b", skills=("expense-report",), label="VAT receipts"))
    env.store.save_trace(make_trace("run_live", "live"))
    async with env.client() as client:
        goldens = (await client.get(f"{V1}/traces")).json()
        by_skill = (await client.get(f"{V1}/traces", params={"skill": "expense-report"})).json()
        by_search = (await client.get(f"{V1}/traces", params={"search": "widg"})).json()
        trace = (await client.get(f"{V1}/traces/run_a")).json()
        blessed = await client.post(f"{V1}/runs/run_live/bless")
        unblessed = await client.post(f"{V1}/traces/run_a/unbless")
        criteria = await client.put(
            f"{V1}/traces/run_a/criteria", json={"criteria": [" Labels ", ""]}
        )
        too_long = await client.put(f"{V1}/traces/run_a/criteria", json={"criteria": ["x" * 301]})
    assert [t["run_id"] for t in goldens["items"]] == ["run_a", "run_b"]
    assert [t["run_id"] for t in by_skill["items"]] == ["run_b"]
    assert [t["run_id"] for t in by_search["items"]] == ["run_a"]
    assert trace["run_id"] == "run_a" and goldens["items"][0]["n_facts"] == 0
    assert blessed.json()["kind"] == "golden" and unblessed.json()["kind"] == "live"
    assert criteria.json()["success_criteria"] == ["Labels"]
    assert too_long.status_code == 422


async def test_costs_tiles_purpose_day_and_counterfactual(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "g1")
    rows = [
        ("c1", "screen", "nano", 1000, 100, 0.0002),
        ("c2", "check", "super", 2000, 200, 0.0012),
    ]
    for call_id, purpose, tier, pt, ct, usd in rows:
        env.store.record_cost(
            CostRow(
                call_id,
                "judge_g1",
                "edit_g1",
                tier,
                "m",
                pt,
                ct,
                usd,
                purpose,  # type: ignore[arg-type]
                cached_prompt_tokens=pt // 4,
            )  # fmt: skip
        )
    env.store.record_cost(CostRow("c3", "run_x", None, "embed", "e", 10, 0, 0.0, "embed"))
    async with env.client() as client:
        costs = (await client.get(f"{V1}/costs")).json()
        ticker = (await client.get(f"{V1}/ticker")).json()
    assert costs["today"]["usd_by_tier"]["super"] == pytest.approx(0.0012)
    assert costs["week"]["tokens_by_tier"]["nano"] == 1100
    assert costs["today"]["prompt_tokens_by_tier"] == {"nano": 1000, "super": 2000, "ultra": 0}
    assert costs["today"]["cached_prompt_tokens_by_tier"]["super"] == 500
    assert costs["week"]["cache_share"] == pytest.approx(0.25)
    assert costs["last_edit"]["cache_share"] is None  # per-verdict tiles leave it unset
    assert {(p["purpose"], p["tier"], p["calls"]) for p in costs["by_purpose"]} == {
        ("screen", "nano", 1),
        ("check", "super", 1),
    }
    assert costs["last_edit"]["edit_id"] == "edit_g1"
    assert costs["counterfactual"]["all_ultra_usd"] == pytest.approx((3000 * 1.0 + 300 * 3.0) / 1e6)
    assert 0 < costs["counterfactual"]["saved_ratio"] < 1
    assert costs["bench_report_url"] is None and len(costs["by_day"]) == 1
    assert ticker["total_usd"] == pytest.approx(0.0014) and ticker["live"] is False


async def test_ticker_is_live_while_an_edit_runs(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_running(env.store, "h1")
    async with env.client() as client:
        assert (await client.get(f"{V1}/ticker")).json()["live"] is True


async def test_digest_latest_404_with_copy_then_returns_digest(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        empty = await client.get(f"{V1}/digest/latest")
        env.backend.digest = DigestView(
            digest_id="digest_1",
            period_start=NOW,
            period_end=later(60),
            narrative="This week the agent learned two skills.",
            verdict_ids=["verdict_a"],
            merged=["verdict_a"],
            blocked=[],
            usd=0.12,
            simulated_models=True,
        )
        full = await client.get(f"{V1}/digest/latest")
    assert empty.status_code == 404 and "ratchet digest run" in empty.json()["error"]["message"]
    assert full.json()["digest_id"] == "digest_1" and full.json()["merged"] == ["verdict_a"]


async def test_events_feed_newest_first_and_since_polling(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    ids = [env.store.emit("edit.detected", f"edit_{i}", {"i": i}) for i in range(5)]
    env.store.emit("pair.decided", "p", {})
    async with env.client() as client:
        newest = (await client.get(f"{V1}/events", params={"limit": 2})).json()
        older = (
            await client.get(f"{V1}/events", params={"limit": 2, "cursor": newest["next_cursor"]})
        ).json()
        since = (await client.get(f"{V1}/events", params={"since": ids[2]})).json()
        kinds = (await client.get(f"{V1}/events", params={"kinds": "pair.decided"})).json()
        bad = await client.get(f"{V1}/events", params={"kinds": "DROP TABLE"})
    assert [e["id"] for e in newest["items"]] == [ids[4] + 1, ids[4]]
    assert [e["id"] for e in older["items"]] == [ids[3], ids[2]]
    assert [e["id"] for e in since["items"]] == [ids[3], ids[4], ids[4] + 1]
    assert [e["kind"] for e in kinds["items"]] == ["pair.decided"]
    assert bad.status_code == 400


async def test_settings_view_on_operator_host(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        body = (await client.get(f"{V1}/settings")).json()
    assert body["caps"][0] == {"purpose": "all", "usd_per_day": 10.0}
    assert body["models"]["nano"] == "ratchet-sim/nano"


async def test_healthz_and_metrics(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "m1")
    env.store.emit("simulate.finished", "x", {"scenario": "1", "status": "done"})
    async with env.client() as client:
        health = (await client.get("/healthz")).json()
        await client.get(f"{V1}/verdicts/verdict_m1")
        metrics = (await client.get("/metrics")).text
    assert health == {"ok": True, "components": {"db": "ok", "simulate": "live"}}
    assert 'ratchet_edits_total{decision="BLOCK"} 1.0' in metrics
    assert 'ratchet_simulate_runs_total{scenario="1",status="done"} 1.0' in metrics
    assert 'ratchet_up{component="db"} 1.0' in metrics
    assert 'route="/api/v1/verdicts/{verdict_id}"' in metrics


async def test_healthz_reports_failing_probe_as_503(tmp_path: Path) -> None:
    env = make_env(tmp_path)

    async def down() -> bool:
        return False

    env.app.state.services.options.probes = {"proxy": down}
    async with env.client() as client:
        res = await client.get("/healthz")
    assert res.status_code == 503 and res.json()["components"]["proxy"] == "down"


async def test_errors_carry_envelope_and_echo_request_id(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        echoed = await client.get(f"{V1}/verdicts/verdict_x", headers={"X-Request-Id": "req_abc"})
        unknown = await client.get(f"{V1}/no/such/route")
        unknown_post = await client.post("/api/nope")
        generated = await client.get(f"{V1}/meta", headers={"X-Request-Id": "bad id!"})
    assert echoed.headers["X-Request-Id"] == "req_abc"
    assert echoed.json()["error"] == {
        "code": "not_found",
        "message": "verdict verdict_x not found",
        "request_id": "req_abc",
    }
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"
    assert unknown_post.status_code == 404
    assert generated.headers["X-Request-Id"].startswith("req_")


async def test_unexpected_exception_is_a_500_envelope_with_request_id(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed_decided(env.store, "x1")

    def boom(store: object, edit_id: str) -> None:
        raise RuntimeError("secret detail")

    env.backend.progress_for = boom  # type: ignore[method-assign,assignment]
    seed_running(env.store, "x2")
    async with env.client() as client:
        res = await client.get(f"{V1}/verdicts/verdict_x2", headers={"X-Request-Id": "req_500"})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "internal_error"
    assert "secret" not in res.text and res.headers["X-Request-Id"] == "req_500"


async def test_request_log_line_has_route_template_and_no_query(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env(tmp_path, ratchet_log_level="INFO")
    async with env.client() as client:
        await client.get(f"{V1}/verdicts/verdict_secret123", params={"cursor": "abc"})
    lines = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    done = [line for line in lines if line["event"] == "api.request.done"]
    assert done and done[-1]["route"] == "/api/v1/verdicts/{verdict_id}"
    assert done[-1]["status"] == 404 and "latency_ms" in done[-1]
    assert "verdict_secret123" not in json.dumps(done) and "127.0.0.1" not in json.dumps(lines)


async def test_costs_accept_a_from_to_window(tmp_path: Path) -> None:
    """GET /costs?from=&to= (ISO) scopes window, by_purpose and by_day."""
    from datetime import timedelta

    from ratchet import clock

    env = make_env(tmp_path)
    old = clock.now() - timedelta(days=20)
    with clock.frozen(old):
        env.store.record_cost(
            CostRow(
                "o1", "run_o", None, "nano", "m", 100, 0, 0.5, "screen", cached_prompt_tokens=40
            )
        )
    env.store.record_cost(CostRow("n1", "run_n", None, "super", "m", 10, 0, 0.25, "check"))
    start = (old - timedelta(days=1)).isoformat()
    end = (old + timedelta(days=1)).isoformat()
    async with env.client() as client:
        scoped = (await client.get(f"{V1}/costs", params={"from": start, "to": end})).json()
        plain = (await client.get(f"{V1}/costs")).json()
        bad = await client.get(f"{V1}/costs", params={"from": end, "to": start})
    assert scoped["window"]["usd_by_tier"] == {"nano": 0.5, "super": 0.0, "ultra": 0.0}
    assert set(scoped["window"]) >= {"from", "to"}
    assert scoped["window"]["cache_share"] == pytest.approx(0.4)
    assert scoped["window"]["cached_prompt_tokens_by_tier"]["nano"] == 40
    assert [(p["purpose"], p["tier"]) for p in scoped["by_purpose"]] == [("screen", "nano")]
    assert [d["day"] for d in scoped["by_day"]] == [old.date().isoformat()]
    assert plain["window"] is None
    assert [(p["purpose"], p["tier"]) for p in plain["by_purpose"]] == [("check", "super")]
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_request"


async def test_oversized_post_bodies_are_refused_with_413(tmp_path: Path) -> None:
    """POST/PUT bodies over ratchet_api_max_body_bytes → 413 payload_too_large, both
    with a Content-Length and when chunked."""
    env = make_env(tmp_path, ratchet_api_max_body_bytes=1024)
    big = b'{"criteria": ["' + b"x" * 4096 + b'"]}'

    async def chunks():  # type: ignore[no-untyped-def]
        for i in range(0, len(big), 512):
            yield big[i : i + 512]

    async with env.client() as client:
        declared = await client.put(
            f"{V1}/traces/run_a/criteria", content=big, headers={"content-type": "application/json"}
        )
        chunked = await client.put(
            f"{V1}/traces/run_a/criteria",
            content=chunks(),
            headers={"content-type": "application/json"},
        )
        small = await client.get(f"{V1}/costs")
    assert declared.status_code == 413 and declared.json()["error"]["code"] == "payload_too_large"
    assert chunked.status_code == 413
    assert small.status_code == 200
