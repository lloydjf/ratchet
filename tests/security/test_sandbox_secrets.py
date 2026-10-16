"""The harness environment inside a sandbox contains no real key."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ratchet.config.settings import Settings
from ratchet.errors import SandboxError
from ratchet.replay.harness.base import HostPaths
from ratchet.replay.sandbox import DockerOptions, DockerSandbox, ProcessSandbox, scrub_env

TF_KEY = "tf-live-key-abcdef0123456789"
TAVILY_KEY = "tvly-live-key-0123456789"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        ratchet_data_dir=tmp_path / "d",
        nebius_token_factory_key=TF_KEY,
        tavily_api_key=TAVILY_KEY,
        nebius_api_key="nebius-iam-token-123456",
        nebius_s3_secret_access_key="s3-secret-0123456789",
    )


def test_process_sandbox_harness_sees_only_the_dummy_key(settings, tmp_path):
    host = HostPaths(work=tmp_path / "w", skills=tmp_path / "s", io=tmp_path / "io")
    for p in (host.work, host.skills, host.io):
        p.mkdir()
    sandbox = ProcessSandbox(settings)
    sandbox.create(host.work, host.skills, {"RATCHET_RUN_ID": "run_1"}, io_dir=host.io)
    code, out, _ = sandbox.run(
        [sys.executable, "-I", "-c", "import os; print(repr(dict(os.environ)))"], 30
    )
    assert code == 0
    for secret in (TF_KEY, TAVILY_KEY, "nebius-iam-token-123456", "s3-secret-0123456789"):
        assert secret not in out
    assert "ratchet-dummy-key" in out
    assert "NEBIUS_TOKEN_FACTORY_KEY" not in out


def test_scrub_env_refuses_a_secret_smuggled_in_an_allowed_var(settings):
    with pytest.raises(SandboxError):
        scrub_env({"RATCHET_PROXY_URL": f"http://x/?k={TF_KEY}"}, settings)


def test_docker_sandbox_command_carries_no_secret(settings, tmp_path):
    host = HostPaths(work=tmp_path / "w", skills=tmp_path / "s", io=tmp_path / "io")
    calls: list[list[str]] = []

    def runner(argv: list[str], timeout: float | None) -> tuple[int, str, str]:
        calls.append(argv)
        return 0, "", ""

    sandbox = DockerSandbox(DockerOptions(image="img"), settings=settings, runner=runner)
    sandbox.create(host.work, host.skills, {"NEBIUS_TOKEN_FACTORY_KEY": TF_KEY}, io_dir=host.io)
    sandbox.run(["python", "-V"], 5)
    assert all(TF_KEY not in " ".join(call) for call in calls)
