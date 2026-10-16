"""Sandboxes: env scrubbing, the process sandbox, Docker/OpenShell command construction."""

from __future__ import annotations

import io
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from ratchet.config.settings import Settings
from ratchet.errors import ConfigError, SandboxError
from ratchet.replay.harness.base import HostPaths
from ratchet.replay.sandbox import (
    TIMEOUT_EXIT,
    DockerOptions,
    DockerSandbox,
    Endpoint,
    OpenShellOptions,
    OpenShellSandbox,
    ProcessSandbox,
    faketime_env,
    get_sandbox,
    scrub_env,
)
from ratchet.replay.sandbox.docker import network_create_argv, run_argv
from ratchet.replay.sandbox.openshell import _extract, create_argv, exec_argv, policy


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, ratchet_data_dir=tmp_path / "d")  # type: ignore[call-arg]


@pytest.fixture()
def host(tmp_path: Path) -> HostPaths:
    paths = HostPaths(work=tmp_path / "w", skills=tmp_path / "s", io=tmp_path / "io")
    for p in (paths.work, paths.skills, paths.io):
        p.mkdir()
    return paths


class FakeRunner:
    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.codes = codes or {}

    def __call__(self, argv: list[str], timeout_s: float | None) -> tuple[int, str, str]:
        self.calls.append(argv)
        return self.codes.get(argv[1] + ":" + argv[2], 0), "out", "err"


def test_scrub_env_drops_unknown_vars_and_forces_dummy_key(settings):
    env = scrub_env({"PATH": "/bin", "AWS_SECRET": "x", "OPENAI_API_KEY": "sk-real"}, settings)
    assert env == {
        "PATH": "/bin",
        "OPENAI_API_KEY": "ratchet-dummy-key",
        "RATCHET_DUMMY_KEY": "ratchet-dummy-key",
    }


def test_scrub_env_drops_non_faketime_preload(settings):
    assert "LD_PRELOAD" not in scrub_env({"LD_PRELOAD": "/tmp/evil.so"}, settings)
    lib = "/usr/local/lib/libfaketime.so.1"
    assert scrub_env({"LD_PRELOAD": lib}, settings)["LD_PRELOAD"] == lib


def test_faketime_env_starts_clock_at_frozen_time():
    env = faketime_env(datetime(2026, 10, 14, 16, 0, tzinfo=UTC), "/lib/libfaketime.so.1")
    assert env["FAKETIME"] == "@2026-10-14 16:00:00"
    assert faketime_env(None, "/lib/x") == {}
    assert faketime_env(datetime(2026, 1, 1, tzinfo=UTC), None) == {}


def test_process_sandbox_runs_in_workspace_with_scrubbed_env(settings, host):
    sandbox = ProcessSandbox(settings)
    sandbox.create(
        host.work, host.skills, {"RATCHET_RUN_ID": "run_1", "SECRET": "x"}, io_dir=host.io
    )
    code, out, _ = sandbox.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import os,json;print(json.dumps([os.getcwd(),dict(os.environ)]))",
        ],
        30,
    )
    assert code == 0
    import json

    cwd, env = json.loads(out)
    assert Path(cwd).resolve() == host.work.resolve()
    assert env["RATCHET_RUN_ID"] == "run_1" and "SECRET" not in env
    assert env["HOME"] == str(host.io)
    sandbox.destroy()


def test_process_sandbox_kills_at_wall_clock(settings, host):
    sandbox = ProcessSandbox(settings)
    sandbox.create(host.work, host.skills, {}, io_dir=host.io)
    code, _, err = sandbox.run([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    assert code == TIMEOUT_EXIT and "wall-clock" in err


def test_process_sandbox_refuses_public_mode(settings, host):
    sandbox = ProcessSandbox(settings.model_copy(update={"ratchet_public_mode": True}))
    with pytest.raises(SandboxError):
        sandbox.create(host.work, host.skills, {}, io_dir=host.io)


def test_docker_run_argv_is_locked_down(host):
    opts = DockerOptions(image="ratchet-worker:dev")
    cmd = run_argv(opts, "sbx1", host, ["python", "-m", "x"], {"B": "2", "A": "1"})
    joined = " ".join(cmd)
    for flag in (
        "--rm",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--network ratchet-sandbox",
        "--pids-limit 256",
        "--memory 2g",
        "--user 10001:10001",
    ):
        assert flag in joined
    assert f"type=bind,src={host.skills},dst=/skills,readonly" in cmd
    assert "/var/run/docker.sock" not in joined
    assert cmd.index("A=1") < cmd.index("B=2")
    assert cmd[-4:] == ["python", "ratchet-worker:dev", "-m", "x"]
    assert cmd[cmd.index("--entrypoint") + 1] == "python"


def test_docker_network_is_internal_without_icc():
    cmd = network_create_argv(DockerOptions(image="i"))
    assert "--internal" in cmd and "com.docker.network.bridge.enable_icc=false" in cmd
    assert cmd[-1] == "ratchet-sandbox"


def test_docker_sandbox_creates_network_once_and_kills_on_timeout(settings, host):
    runner = FakeRunner({"network:inspect": 1})
    sandbox = DockerSandbox(DockerOptions(image="img"), settings=settings, runner=runner)
    assert sandbox.bind_host == sandbox.connect_host == "172.30.0.1"
    sandbox.create(host.work, host.skills, {"OPENAI_API_KEY": "real"}, io_dir=host.io)
    assert runner.calls[1][:3] == ["docker", "network", "create"]
    runner.codes["run:--rm"] = TIMEOUT_EXIT
    code, _, _ = sandbox.run(["python", "-V"], 5)
    assert code == TIMEOUT_EXIT and runner.calls[-1][:2] == ["docker", "kill"]
    run_cmd = runner.calls[-2]
    assert "OPENAI_API_KEY=ratchet-dummy-key" in run_cmd and "OPENAI_API_KEY=real" not in run_cmd
    sandbox.destroy()
    assert runner.calls[-1][:3] == ["docker", "rm", "-f"]


def test_openshell_policy_denies_all_but_sidecars():
    opts = OpenShellOptions(image="img")
    doc = policy(
        opts, [Endpoint("host.openshell.internal", 4001), Endpoint("host.openshell.internal", 4002)]
    )
    assert doc["version"] == 1
    assert doc["landlock"] == {"compatibility": "hard_requirement"}
    rules = doc["network_policies"]
    assert sorted(r["endpoints"][0]["port"] for r in rules.values()) == [4001, 4002]
    for rule in rules.values():
        assert rule["endpoints"][0]["enforcement"] == "enforce"
        assert rule["binaries"]
    fs = doc["filesystem_policy"]
    assert "/sandbox/skills" in fs["read_only"] and "/sandbox/skills" not in fs["read_write"]
    yaml.safe_dump(doc)  # serializable


def test_openshell_commands(host, tmp_path):
    opts = OpenShellOptions(image="img")
    cmd = create_argv(opts, "sbx", tmp_path / "p.yaml", host, {"K": "V"})
    assert cmd[:5] == ["openshell", "sandbox", "create", "--name", "sbx"]
    assert f"{host.work}:/sandbox/work" in cmd and cmd[-3:] == ["--", "sleep", "infinity"]
    assert exec_argv(opts, "sbx", ["python", "-V"], 12.7) == [
        "openshell",
        "sandbox",
        "exec",
        "-n",
        "sbx",
        "--timeout",
        "12",
        "--",
        "python",
        "-V",
    ]


def test_openshell_sandbox_lifecycle_writes_policy(settings, host):
    runner = FakeRunner()
    sandbox = OpenShellSandbox(OpenShellOptions(image="img"), settings=settings, runner=runner)
    sandbox.create(host.work, host.skills, {}, io_dir=host.io, egress=[Endpoint("127.0.0.1", 5)])
    written = yaml.safe_load((host.io.parent / "openshell-policy.yaml").read_text())
    assert written["network_policies"]["ratchet_sidecar_0"]["endpoints"][0]["port"] == 5
    sandbox.destroy()
    assert runner.calls[-1][:3] == ["openshell", "sandbox", "delete"]


def test_openshell_sync_back_refuses_escaping_members(tmp_path):
    def tar_with(name: str) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        return buf.getvalue()

    targets = {"work": tmp_path / "w", "io": tmp_path / "io"}
    _extract(tar_with("work/ok.txt"), targets)
    assert (tmp_path / "w" / "ok.txt").read_text() == "x"
    with pytest.raises(SandboxError):
        _extract(tar_with("work/../../evil"), targets)
    _extract(tar_with("etc/passwd"), targets)  # outside the two trees: ignored
    assert not (tmp_path / "etc").exists()


def test_get_sandbox_by_name(settings):
    assert isinstance(get_sandbox("process", settings), ProcessSandbox)
    assert isinstance(get_sandbox("docker", settings), DockerSandbox)
    assert isinstance(get_sandbox("openshell", settings), OpenShellSandbox)
    with pytest.raises(ConfigError):
        get_sandbox("vm", settings)
