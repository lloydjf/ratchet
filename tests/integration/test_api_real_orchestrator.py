"""The dashboard API against the real orchestrator modules (orchestrator-api-cli.md §11).

Each test is skipped, with the reason, when the module it needs is not importable yet (the
orchestrator is built in parallel). Everything else runs against the real code paths: the
default ``Backend`` imports the modules lazily, exactly as in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_factories import DIFF, make_edit, make_trace, seed_decided  # noqa: E402
from test_api_fakes import api_reset  # noqa: E402

from ratchet.config.settings import Settings, configure  # noqa: E402
from ratchet.dashboard_api.app import create_app  # noqa: E402
from ratchet.dashboard_api.deps import ApiOptions  # noqa: E402
from ratchet.dashboard_api.views import Progress, ScenarioView  # noqa: E402
from ratchet.store import Store  # noqa: E402

__all__ = ["api_reset"]
V1 = "/api/v1"
REPO = Path(__file__).resolve().parents[2]
_WHY = "ratchet.orchestrator.{} is not importable yet (built in parallel, design §11)"


def _need(name: str) -> None:
    pytest.importorskip(f"ratchet.orchestrator.{name}", reason=_WHY.format(name))


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "ratchet_data_dir": tmp_path / "data",
        "ratchet_skills_repo": tmp_path / "skills",
        "ratchet_scenarios_dir": REPO / "scenarios",
        "ratchet_dashboard_dist": tmp_path / "dist",
        "ratchet_upstream": "sim",
        "ratchet_public_mode": False,
        "ratchet_simulate_enabled": True,
        "ratchet_log_level": "WARNING",
    }
    values.update(overrides)
    return configure(Settings.model_validate(values))


def _client(settings: Settings, store: Store, orchestrator: object = None) -> httpx.AsyncClient:
    app = create_app(settings, store, orchestrator, options=ApiOptions())  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_real_progress_feeds_the_running_verdict_view(tmp_path: Path) -> None:
    _need("progress")
    settings = _settings(tmp_path)
    store = Store.open(settings.ratchet_data_dir)
    edit = store.save_edit(make_edit("edit_run1"), DIFF)
    store.emit("edit.detected", edit.edit_id, {"edit_id": edit.edit_id})
    store.set_edit_state(edit.edit_id, "collecting")
    async with _client(settings, store) as client:
        res = await client.get(f"{V1}/verdicts/verdict_run1")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verdict"] is None and body["state"] == "collecting"
    progress = Progress.model_validate(body["progress"])
    assert progress.stages and progress.stage


async def test_real_ids_preallocate_the_verdict_id_on_edit_views(tmp_path: Path) -> None:
    _need("ids")
    settings = _settings(tmp_path)
    store = Store.open(settings.ratchet_data_dir)
    store.save_edit(make_edit("edit_abc123"), DIFF)
    async with _client(settings, store) as client:
        body = (await client.get(f"{V1}/edits/edit_abc123")).json()
    assert body["verdict_id"] == "verdict_abc123"


async def test_real_simulate_service_lists_the_repo_scenarios(tmp_path: Path) -> None:
    _need("simulate")
    _need("service")
    from ratchet.orchestrator.service import Orchestrator

    settings = _settings(tmp_path)
    store = Store.open(settings.ratchet_data_dir)
    orchestrator = Orchestrator(settings, store)
    async with _client(settings, store, orchestrator) as client:
        res = await client.get(f"{V1}/scenarios")
        unknown = await client.post(f"{V1}/simulate/no-such-scenario")
    assert res.status_code == 200, res.text
    scenarios = [ScenarioView.model_validate(s) for s in res.json()]
    assert {"1", "2", "3"} <= {s.scenario_id for s in scenarios}
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"


async def test_real_bless_module_unblesses_and_sets_criteria(tmp_path: Path) -> None:
    _need("bless")
    settings = _settings(tmp_path)
    store = Store.open(settings.ratchet_data_dir)
    store.save_trace(make_trace("run_gold"))
    async with _client(settings, store) as client:
        criteria = await client.put(
            f"{V1}/traces/run_gold/criteria", json={"criteria": ["Labels every issue"]}
        )
        unblessed = await client.post(f"{V1}/traces/run_gold/unbless")
        again = await client.post(f"{V1}/traces/run_gold/unbless")
    assert criteria.json()["success_criteria"] == ["Labels every issue"]
    assert unblessed.json()["kind"] == "live"
    assert again.status_code == 409


async def test_real_actions_map_unknown_and_locked_verdicts(tmp_path: Path) -> None:
    _need("actions")
    settings = _settings(tmp_path)
    store = Store.open(settings.ratchet_data_dir)
    seed_decided(store, "lock1")
    store.acquire_action_lock("verdict_lock1")
    async with _client(settings, store) as client:
        missing = await client.post(f"{V1}/verdicts/verdict_missing/rollback")
        locked = await client.post(f"{V1}/verdicts/verdict_lock1/approve")
    assert missing.status_code == 404
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "action_locked"


async def test_real_digest_is_404_until_one_exists(tmp_path: Path) -> None:
    _need("digest")
    settings = _settings(tmp_path)
    store = Store.open(settings.ratchet_data_dir)
    async with _client(settings, store) as client:
        res = await client.get(f"{V1}/digest/latest")
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
