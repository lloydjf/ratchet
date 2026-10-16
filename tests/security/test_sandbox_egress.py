"""From inside the Docker sandbox, the internet is unreachable; sidecars are not.

Needs a Docker daemon and the ``ratchet-worker`` image (``docker build -f worker/Dockerfile
-t ratchet-worker:dev .``); excluded by default (``-m docker`` to run). Needs a Linux host
network namespace (the bridge gateway must be a local interface): on macOS run it inside the
``control-docker`` image with ``--network host`` (docs/research/docker-sandbox-s1.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

from ratchet.config.settings import Settings
from ratchet.replay.harness.base import HostPaths
from ratchet.replay.sandbox import DockerOptions, DockerSandbox
from ratchet.replay.sidecars import ServerThread

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason=(
            "The egress test needs a Linux host: the ratchet-sandbox bridge gateway (172.30.0.1) "
            "must be a host interface, which it is not under Docker Desktop/OrbStack on macOS; "
            "run it in the control-docker container with --network host "
            "(docs/research/docker-sandbox-s1.md)"
        ),
    ),
]

PROBE = (
    "import sys,urllib.request\n"
    "def ok(u):\n"
    "    try:\n"
    "        urllib.request.urlopen(u, timeout=5); return True\n"
    "    except Exception:\n"
    "        return False\n"
    "print(ok(sys.argv[1]), ok('http://example.com'))\n"
)


def test_docker_sandbox_blocks_internet_but_reaches_sidecar(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "d")  # type: ignore[call-arg]
    sandbox = DockerSandbox(DockerOptions(image="ratchet-worker:dev"), settings=settings)
    host = HostPaths(work=tmp_path / "w", skills=tmp_path / "s", io=tmp_path / "io")
    for p in (host.work, host.skills, host.io):
        p.mkdir()
    (host.io / "probe.py").write_text(PROBE)
    app = FastAPI()
    app.get("/healthz")(lambda: {"ok": True})
    sandbox.preflight()  # on Linux an unbindable gateway is a failure, not a skip
    with ServerThread(app, sandbox.bind_host, hint=sandbox.bind_hint) as sidecar:
        sandbox.create(host.work, host.skills, {}, io_dir=host.io)
        code, out, _ = sandbox.run(["python", "/io/probe.py", f"{sidecar.url}/healthz"], 60)
        sandbox.destroy()
    assert code == 0
    assert out.split() == ["True", "False"]
