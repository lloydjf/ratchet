"""LocalJobRunner helpers: settings → worker env, docker worker command, lost jobs."""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.config.settings import Settings
from ratchet.errors import NotFoundError
from ratchet.models import ReplayBatch
from ratchet.replay.jobs import LocalJobRunner, NebiusJobRunner, get_runner
from ratchet.replay.jobs.local import docker_worker_argv, settings_env


def _spec(sandbox: str = "docker") -> ReplayBatch:
    return ReplayBatch(
        batch_id="batch_1",
        edit_id="edit_1",
        golden_run_ids=["run_a"],
        skills_tar_ref="s",
        cassettes_ref="c",
        worker_image="ratchet-worker:abc",
        agent_tier="super",
        sandbox=sandbox,  # type: ignore[arg-type]
    )


def test_settings_env_round_trips_through_settings(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        ratchet_data_dir=Path("rel/data"),
        nebius_token_factory_key="tf-key-123456789",
        ratchet_purpose_usd_caps={"agent": 5.0},
        ratchet_public_mode=True,
    )
    env = settings_env(settings)
    assert env["NEBIUS_TOKEN_FACTORY_KEY"] == "tf-key-123456789"
    assert env["RATCHET_PUBLIC_MODE"] == "true"
    assert Path(env["RATCHET_DATA_DIR"]).is_absolute()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    rebuilt = Settings(_env_file=None)  # type: ignore[call-arg]
    assert rebuilt.ratchet_purpose_usd_caps == {"agent": 5.0}
    assert rebuilt.ratchet_public_mode is True


def test_docker_worker_argv_mounts_io_and_passes_env_file(tmp_path):
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    cmd = docker_worker_argv(settings, _spec(), tmp_path / "in", tmp_path / "out", tmp_path / "e")
    assert cmd[:3] == ["docker", "run", "-d"]
    assert f"type=bind,src={(tmp_path / 'in').resolve()},dst=/in,readonly" in cmd
    assert cmd[cmd.index("--env-file") + 1] == str(tmp_path / "e")
    assert cmd[-5:] == ["ratchet-worker:abc", "--inputs", "/in", "--output", "/out"]
    process_cmd = docker_worker_argv(settings, _spec("process"), tmp_path, tmp_path, tmp_path)
    assert "/var/run/docker.sock" not in " ".join(process_cmd)


async def test_unknown_local_job_without_marker_is_failed(tmp_path):
    runner = LocalJobRunner("process", settings=Settings(_env_file=None), work_root=tmp_path)  # type: ignore[call-arg]
    status = await runner.poll("local-batch_missing")
    assert status.state == "failed" and status.exit_code is None
    with pytest.raises(NotFoundError):
        await runner.poll("../etc")


def test_get_runner_follows_settings():
    assert isinstance(get_runner(Settings(_env_file=None)), LocalJobRunner)  # type: ignore[call-arg]
    nebius = Settings(_env_file=None, ratchet_job_runner="nebius")  # type: ignore[call-arg]
    assert isinstance(get_runner(nebius), NebiusJobRunner)


async def test_restarted_runner_adopts_a_live_worker_instead_of_running_it_twice(
    tmp_path: Path,
) -> None:
    """Orchestrator restart mid-collecting: a new runner over the same work root must adopt
    the still-running worker (no second process), report it running, and be able to stop it."""
    import subprocess
    import sys

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    first = LocalJobRunner("process", settings=settings, work_root=tmp_path)
    job_id = "local-batch_1"
    out = first.output_dir(job_id)
    out.mkdir(parents=True)
    # A stand-in worker whose command line looks like the real one.
    sleeper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            "ratchet.replay.worker",
            "--output",
            str(out.resolve()),
        ]
    )
    try:
        (out.parent / "worker.pid").write_text(str(sleeper.pid), encoding="utf-8")
        restarted = LocalJobRunner("process", settings=settings, work_root=tmp_path)
        assert await restarted.submit(_spec("process"), tmp_path / "inputs") == job_id
        assert restarted._procs == {}  # adopted, not relaunched
        assert (await restarted.poll(job_id)).state == "running"
        await restarted.cancel(job_id)
        assert sleeper.wait(timeout=10) != 0
        assert (await restarted.poll(job_id)).state == "failed"  # lost: no exit marker
    finally:
        if sleeper.poll() is None:
            sleeper.kill()


async def test_stale_pid_file_for_an_unrelated_process_is_not_adopted(tmp_path: Path) -> None:
    import os

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    runner = LocalJobRunner("process", settings=settings, work_root=tmp_path)
    job_id = "local-batch_1"
    out = runner.output_dir(job_id)
    out.mkdir(parents=True)
    (out.parent / "worker.pid").write_text(str(os.getpid()), encoding="utf-8")  # pytest itself
    assert runner._orphan_pid(job_id) is None
