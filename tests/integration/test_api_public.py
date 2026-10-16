"""Public demo host controls (docs/design/orchestrator-api-cli.md §8)."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_factories import later, seed_decided  # noqa: E402
from test_api_fakes import ApiEnv, api_reset, make_env  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.dashboard_api.security import TokenBucket, action_token  # noqa: E402

__all__ = ["api_reset"]
V1 = "/api/v1"
TOKEN = "X-Ratchet-Action-Token"


@asynccontextmanager
async def _closing(client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    try:
        yield client
    finally:
        await client.aclose()


async def _session(env: ApiEnv, ip: str = "127.0.0.1") -> tuple[httpx.AsyncClient, str, str]:
    """A client that has called /meta: (client, session_hash via simulate, action token)."""
    client = env.client(ip)
    meta = (await client.get(f"{V1}/meta")).json()
    start = await client.post(f"{V1}/simulate/1", headers={TOKEN: meta["action_token"]})
    assert start.status_code == 200, start.text
    return client, start.json()["session_hash"], meta["action_token"]


async def test_meta_serves_todays_action_token_in_public_mode(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    async with env.client() as client:
        with clock.frozen(datetime(2026, 10, 20, 9, 0, tzinfo=UTC)):
            body = (await client.get(f"{V1}/meta")).json()
    assert body["public_mode"] is True
    assert body["action_token"] == action_token("test-secret", datetime(2026, 10, 20).date())
    assert len(body["action_token"]) == 24


async def test_actions_require_the_action_token_in_public_mode(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    client, session_hash, token = await _session(env)
    seed_decided(env.store, "own1", session_hash=session_hash)
    own = "verdict_own1"
    async with _closing(client):
        missing = await client.post(f"{V1}/verdicts/{own}/rollback")
        wrong = await client.post(f"{V1}/verdicts/{own}/rollback", headers={TOKEN: "0" * 24})
        ok = await client.post(f"{V1}/verdicts/{own}/rollback", headers={TOKEN: token})
    assert missing.status_code == 403 and missing.json()["error"]["code"] == "action_token_invalid"
    assert wrong.status_code == 403
    assert ok.status_code == 200, ok.text
    assert env.backend.calls == [("rollback", own, "judge")]


async def test_token_rotates_daily_with_one_hour_grace_for_yesterday(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    day1 = datetime(2026, 10, 20, 23, 30, tzinfo=UTC)
    async with env.client() as client:
        with clock.frozen(day1):
            old = (await client.get(f"{V1}/meta")).json()["action_token"]
        with clock.frozen(datetime(2026, 10, 21, 0, 30, tzinfo=UTC)):
            new = (await client.get(f"{V1}/meta")).json()["action_token"]
            grace = await client.post(f"{V1}/simulate/1", headers={TOKEN: old})
        with clock.frozen(datetime(2026, 10, 21, 1, 0, tzinfo=UTC)):
            expired = await client.post(f"{V1}/simulate/1", headers={TOKEN: old})
            current = await client.post(f"{V1}/simulate/2", headers={TOKEN: new})
    assert old != new
    assert grace.status_code == 200
    assert expired.status_code == 403 and expired.json()["error"]["code"] == "action_token_invalid"
    assert current.status_code == 200


async def test_eleventh_action_in_a_minute_is_rate_limited(tmp_path: Path) -> None:
    now = [1000.0]
    env = make_env(tmp_path, limiter=TokenBucket(10, 60.0, monotonic=lambda: now[0]))
    async with env.client("10.0.0.1") as client, env.client("10.0.0.2") as other:
        codes = [(await client.post(f"{V1}/simulate/1")).status_code for _ in range(11)]
        elsewhere = await other.post(f"{V1}/simulate/1")
        now[0] += 6.0  # one token refills every 6 s
        refilled = await client.post(f"{V1}/simulate/1")
        limited = await client.post(f"{V1}/simulate/1")
    assert codes[:10] == [200] * 10 and codes[10] == 429
    assert elsewhere.status_code == 200 and refilled.status_code == 200
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


async def test_bless_unbless_criteria_settings_and_library_are_not_public(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    seed_decided(env.store, "p1")
    async with env.client() as client:
        token = (await client.get(f"{V1}/meta")).json()["action_token"]
        headers = {TOKEN: token}
        responses = [
            await client.post(f"{V1}/runs/run_gp1/bless"),  # no token: still not_public
            await client.post(f"{V1}/traces/run_gp1/unbless", headers=headers),
            await client.put(
                f"{V1}/traces/run_gp1/criteria", json={"criteria": []}, headers=headers
            ),
            await client.get(f"{V1}/traces"),
            await client.get(f"{V1}/settings"),
            # replay re-runs a whole pipeline outside Simulate's per-visitor gate
            await client.post(f"{V1}/edits/edit_p1/replay", headers=headers),
        ]
    for res in responses:
        assert res.status_code == 403 and res.json()["error"]["code"] == "not_public", res.text
    assert "Run Ratchet locally" in responses[0].json()["error"]["message"]
    assert env.backend.calls == []


async def test_public_traces_only_when_cited_by_a_visible_verdict(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    seed_decided(env.store, "q1")  # global demo history
    client, mine, _ = await _session(env)
    seed_decided(env.store, "q2", session_hash=mine)
    seed_decided(env.store, "q3", session_hash="someoneelse12345")
    async with _closing(client):
        global_trace = await client.get(f"{V1}/traces/run_gq1")
        own_trace = await client.get(f"{V1}/traces/run_rq2")
        other_trace = await client.get(f"{V1}/traces/run_rq3")
        pair = await client.get(f"{V1}/verdicts/verdict_q2/pairs/run_gq2/run_rq2")
    assert global_trace.status_code == 200 and own_trace.status_code == 200
    assert other_trace.status_code == 403
    assert pair.status_code == 200


async def test_two_sessions_never_see_each_others_rows_in_lists(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    seed_decided(env.store, "g0")
    alice, a_hash, _ = await _session(env, "10.0.0.1")
    bob, b_hash, _ = await _session(env, "10.0.0.2")
    seed_decided(env.store, "a1", session_hash=a_hash, at=later(1))
    seed_decided(env.store, "b1", session_hash=b_hash, at=later(2))
    async with _closing(alice), _closing(bob), env.client() as anon:
        a_verdicts = (await alice.get(f"{V1}/verdicts")).json()["items"]
        a_events = (await alice.get(f"{V1}/events", params={"limit": 200})).json()["items"]
        a_edits = (await alice.get(f"{V1}/edits")).json()["items"]
        b_verdicts = (await bob.get(f"{V1}/verdicts")).json()["items"]
        anon_events = (await anon.get(f"{V1}/events")).json()["items"]
        a_deep = await alice.get(f"{V1}/verdicts/verdict_a1")
        a_global = await alice.get(f"{V1}/verdicts/verdict_g0")
    assert {v["verdict_id"] for v in a_verdicts} == {"verdict_a1", "verdict_g0"}
    assert {v["verdict_id"] for v in b_verdicts} == {"verdict_b1", "verdict_g0"}
    assert {e["session_hash"] for e in a_events} == {a_hash, None}
    assert all(e["edit"]["session_hash"] in (a_hash, None) for e in a_edits)
    assert {e["session_hash"] for e in anon_events} == {None}
    assert a_deep.status_code == 200 and a_global.status_code == 200


async def test_public_actions_only_on_the_callers_own_verdicts(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    seed_decided(env.store, "shared")
    alice, a_hash, token = await _session(env, "10.0.0.1")
    seed_decided(env.store, "bobs", session_hash="bobsession123456")
    seed_decided(env.store, "alices", session_hash=a_hash)
    headers = {TOKEN: token}
    async with _closing(alice):
        shared = await alice.post(f"{V1}/verdicts/verdict_shared/approve", headers=headers)
        other = await alice.post(f"{V1}/verdicts/verdict_bobs/rollback", headers=headers)
        rebless_other = await alice.post(f"{V1}/pairs/run_gbobs/run_rbobs/rebless", headers=headers)
        rebless_own = await alice.post(
            f"{V1}/pairs/run_galices/run_ralices/rebless", headers=headers
        )
        replay_shared = await alice.post(f"{V1}/edits/edit_shared/replay", headers=headers)
    for res in (shared, other, rebless_other, replay_shared):
        assert res.status_code == 403 and res.json()["error"]["code"] == "not_public"
    assert rebless_own.status_code == 200
    assert env.backend.calls == [("rebless", "run_galices", "run_ralices", a_hash)]


async def test_demo_events_store_only_allowlisted_fields(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    landing = {
        "name": "demo.landing_view",
        "session_hash": "a1b2c3d4e5f6a1b2c3d4e5f6",
        "ts": "2026-10-15T12:00:00Z",
        "device": "mobile",
        "referrer": "devpost",
        "props": {},
    }
    verdict_view = {
        "kind": "demo.verdict_view",
        "session_hash": "a1b2c3d4e5f6a1b2c3d4e5f6",
        "device": "desktop",
        "props": {"scenario": "1", "verdict_id": "verdict_abc", "seconds": 41},
    }
    headers = {"User-Agent": "Mozilla/5.0 Secret", "X-Forwarded-For": "203.0.113.9"}
    async with env.client("198.51.100.7") as client:
        ok1 = await client.post(f"{V1}/demo/events", json=landing, headers=headers)
        ok2 = await client.post(f"{V1}/demo/events", json=verdict_view, headers=headers)
    assert ok1.status_code == 204 and ok2.status_code == 204
    rows = env.store.events(table="demo_events")
    assert [r.kind for r in rows] == ["demo.landing_view", "demo.verdict_view"]
    assert rows[0].payload == {"device": "mobile", "referrer": "devpost"}
    assert rows[1].payload == {"scenario": "1", "verdict_id": "verdict_abc", "seconds": 41}
    assert rows[1].session_hash == "a1b2c3d4e5f6a1b2c3d4e5f6"
    dump = json.dumps([r.model_dump(mode="json") for r in rows])
    raw_db = (env.tmp_path / "data" / "ratchet.db").read_bytes()
    for leaked in ("198.51.100.7", "203.0.113.9", "Mozilla"):
        assert leaked not in dump and leaked.encode() not in raw_db


async def test_demo_events_reject_unknown_kinds_and_fields(tmp_path: Path) -> None:
    env = make_env(tmp_path, public=True)
    base = {"kind": "demo.evidence_open", "session_hash": "abcdefgh12345678"}
    cases = [
        {**base, "kind": "demo.unknown"},
        {**base, "props": {"which": "everything"}},
        {**base, "props": {"which": "diff", "email": "x@y.z"}},
        {**base, "ip": "1.2.3.4"},
        {**base, "user_agent": "curl"},
        {**base, "session_hash": "short"},
    ]
    async with env.client() as client:
        codes = [(await client.post(f"{V1}/demo/events", json=c)).status_code for c in cases]
        good = await client.post(f"{V1}/demo/events", json={**base, "props": {"which": "diff"}})
    assert codes[0] == 422 and all(c in (400, 422) for c in codes)
    assert good.status_code == 204
    assert len(env.store.events(table="demo_events")) == 1


async def test_demo_events_are_off_on_the_operator_host(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        res = await client.post(
            f"{V1}/demo/events", json={"kind": "demo.video_click", "session_hash": "abcdefgh1234"}
        )
    assert res.status_code == 404
    assert env.store.events(table="demo_events") == []


async def test_public_trace_visibility_is_cached_per_session(tmp_path: Path) -> None:
    """GET /traces/{id} on the public host does not reload every visible verdict
    per request; a miss refreshes at most once a second per session."""
    env = make_env(tmp_path, public=True)
    seed_decided(env.store, "c1")
    cache = env.app.state.services.visible_traces
    async with _closing(env.client()) as client:
        for _ in range(5):
            assert (await client.get(f"{V1}/traces/run_gc1")).status_code == 200
        for _ in range(5):
            assert (await client.get(f"{V1}/traces/run_nope")).status_code == 403
        assert cache.loads == 1  # misses inside refresh_s reuse the cached set
        seed_decided(env.store, "c2")
        cache.refresh_s = 0.0  # pretend a second passed
        assert (await client.get(f"{V1}/traces/run_gc2")).status_code == 200
    assert cache.loads == 2
