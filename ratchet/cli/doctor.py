"""``ratchet doctor``: settings, keys, model ids, runner, sandbox, Tavily, git, disk, caps.

Each check returns ``(name, status, detail)`` with status ``ok``, ``warn`` or ``fail``;
any ``fail`` makes the command exit non-zero. In live mode the model ids are checked with
``GET /models`` on the configured upstream; nothing else calls the network.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from ratchet.config.models import spec_for
from ratchet.config.settings import Settings
from ratchet.errors import RatchetError
from ratchet.models import TIERS
from ratchet.store import Store
from ratchet.watcher.gitops import SkillsRepo

__all__ = ["Check", "run_checks"]

Status = Literal["ok", "warn", "fail"]
_MIN_FREE_BYTES = 1 << 30


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def _upstream(s: Settings) -> Check:
    if s.simulated:
        return Check("models", "ok", "offline simulator (verdicts are labeled Simulated models)")
    if s.nebius_token_factory_key is None:
        return Check("models", "fail", "NEBIUS_TOKEN_FACTORY_KEY is not set")
    try:
        response = httpx.get(
            s.nebius_tf_base_url + "models",
            headers={"Authorization": f"Bearer {s.nebius_token_factory_key.get_secret_value()}"},
            timeout=15.0,
        )
        response.raise_for_status()
        ids = {str(m.get("id")) for m in response.json().get("data", [])}
    except (httpx.HTTPError, ValueError) as exc:
        return Check("models", "fail", f"GET /models failed ({type(exc).__name__})")
    missing = [t for t in TIERS if spec_for(t, s).model_id not in ids]
    if missing:
        return Check("models", "fail", f"model ids not in the catalog for: {', '.join(missing)}")
    return Check("models", "ok", "nano, super and ultra ids found in the catalog")


def _runner(s: Settings) -> Check:
    if s.ratchet_job_runner == "nebius":
        need = {
            "NEBIUS_API_KEY": s.nebius_api_key,
            "NEBIUS_PROJECT_ID": s.nebius_project_id,
            "NEBIUS_S3_BUCKET": s.nebius_s3_bucket,
        }
        missing = [k for k, v in need.items() if not v]
        if missing:
            return Check("runner", "fail", f"nebius runner needs {', '.join(missing)}")
        return Check("runner", "ok", "nebius Serverless Jobs")
    if s.ratchet_local_runner_mode == "docker" and shutil.which("docker") is None:
        return Check("runner", "fail", "local docker runner but docker is not installed")
    return Check("runner", "ok", f"local ({s.ratchet_local_runner_mode})")


def _sandbox(s: Settings) -> Check:
    if s.ratchet_sandbox == "process":
        if s.ratchet_public_mode:
            return Check("sandbox", "fail", "process sandbox is refused in public mode (ADR 0017)")
        return Check("sandbox", "warn", "process sandbox: dev only, egress not enforced")
    tool = "docker" if s.ratchet_sandbox == "docker" else "openshell"
    if shutil.which(tool) is None:
        return Check("sandbox", "fail", f"{tool} not found on PATH")
    return Check("sandbox", "ok", s.ratchet_sandbox)


def _tavily(s: Settings) -> Check:
    if s.ratchet_tavily_mode == "live":
        if s.tavily_api_key is None:
            return Check("tavily", "fail", "live mode but TAVILY_API_KEY is not set")
        return Check("tavily", "ok", "live")
    if not s.ratchet_tavily_fixtures_dir.is_dir():
        return Check("tavily", "warn", f"fixtures dir {s.ratchet_tavily_fixtures_dir} missing")
    return Check("tavily", "ok", "recorded fixtures")


def _git(s: Settings) -> Check:
    repo = SkillsRepo(s.ratchet_skills_repo)
    if not (repo.main_dir / ".git").exists():
        return Check("git", "warn", "no skills repo yet; run `ratchet seed-demo`")
    if not (repo.candidate_dir / ".git").exists():
        return Check("git", "fail", "candidate worktree missing")
    try:
        main, cand = repo.main_head(), repo.candidate_head()
    except RatchetError as exc:
        return Check("git", "fail", f"git error {exc.code}")
    ahead = "candidate ahead of main" if main != cand else "main == candidate"
    return Check("git", "ok", f"main {main[:8]}, {ahead}")


def _disk(s: Settings) -> Check:
    s.ratchet_data_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(s.ratchet_data_dir)
    used = usage.used / usage.total if usage.total else 0.0
    detail = f"{usage.free // (1 << 20)} MiB free ({used:.0%} used)"
    if usage.free < _MIN_FREE_BYTES:
        return Check("disk", "fail", detail)
    return Check("disk", "warn" if used > 0.8 else "ok", detail)


def _caps(s: Settings, store: Store) -> Check:
    from ratchet import clock

    today = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    spent = store.spend(since=today).total_usd
    detail = f"${spent:.4f} of ${s.ratchet_daily_usd_cap:.2f} today"
    if s.ratchet_daily_usd_cap <= 0:
        return Check("caps", "fail", "daily cap must be positive")
    return Check("caps", "warn" if spent >= 0.8 * s.ratchet_daily_usd_cap else "ok", detail)


def _scenarios(s: Settings) -> Check:
    if not s.ratchet_simulate_enabled:
        return Check("scenarios", "ok", "Simulate disabled")
    from ratchet.orchestrator.scenarios import load_scenarios

    found = load_scenarios(s.ratchet_scenarios_dir)
    if not found:
        return Check("scenarios", "fail", f"no scenarios under {s.ratchet_scenarios_dir}")
    return Check("scenarios", "ok", f"{len(found)} scenarios, mode {s.ratchet_simulate_mode}")


def run_checks(s: Settings, store: Store) -> list[Check]:
    checks: list[Callable[[], Check]] = [
        lambda: Check(
            "settings", "ok", f"upstream {s.ratchet_upstream}, data {s.ratchet_data_dir}"
        ),
        lambda: _upstream(s),
        lambda: _runner(s),
        lambda: _sandbox(s),
        lambda: _tavily(s),
        lambda: _git(s),
        lambda: _disk(s),
        lambda: _caps(s, store),
        lambda: _scenarios(s),
    ]
    results: list[Check] = []
    for check in checks:
        try:
            results.append(check())
        except (RatchetError, OSError) as exc:
            results.append(Check("check", "fail", f"{type(exc).__name__}"))
    return results
