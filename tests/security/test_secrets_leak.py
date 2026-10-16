"""No secret values or key patterns in traces, cassettes, logs or the git tree.

A short in-process run with fake keys configured drives the capture proxy (against the
scripted fake Token Factory from ``tests/fixtures/proxy/``) and the mock tool server in
record mode, captures every log line, then runs ``scripts/secret_scan.py`` — the same
scanner CI runs over the git tree — over the data dir and the logs.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ratchet.config.settings import Settings, reset_settings

ROOT = Path(__file__).resolve().parents[2]
CHAT = "/v1/chat/completions"

# Built at runtime so this file itself never contains a key-shaped literal.
FAKE_TAVILY = "tvly-" + "Q7rT" * 8
FAKE_NEBIUS_IAM = "nbx-iam-" + "k9Lm" * 8
FAKE_S3_SECRET = "s3sec-" + "Zp4W" * 8
FAKE_ACTION = "act-secret-" + "H2vB" * 6
FAKE_SK = "sk-" + "Abc123Def456" * 2


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fu = _load("ratchet_test_fake_upstream", ROOT / "tests" / "fixtures" / "proxy" / "fake_upstream.py")
ss = _load("ratchet_secret_scan", ROOT / "scripts" / "secret_scan.py")

SECRETS = [fu.FAKE_KEY, FAKE_TAVILY, FAKE_NEBIUS_IAM, FAKE_S3_SECRET, FAKE_ACTION]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_settings()


def _secret_overrides() -> dict[str, object]:
    return {
        "tavily_api_key": SecretStr(FAKE_TAVILY),
        "nebius_api_key": SecretStr(FAKE_NEBIUS_IAM),
        "nebius_s3_secret_access_key": SecretStr(FAKE_S3_SECRET),
        "ratchet_action_token_secret": SecretStr(FAKE_ACTION),
    }


def _scan(*paths: Path) -> list[object]:
    scanner = ss.Scanner(SECRETS)
    return list(scanner.scan_paths(paths))


def _file_count(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


async def _drive_proxy(env: object) -> None:
    """Plain, streamed, tool-calling, retried and failing calls, some with hostile inputs."""
    fake = env.fake  # type: ignore[attr-defined]
    leaky_error = {"error": {"message": f"invalid api key {fu.FAKE_KEY}", "type": "auth"}}
    fake.push(
        fu.chat_json("plain answer"),
        fu.sse(fu.sse_chunks(["str", "eam"], usage={"prompt_tokens": 5, "completion_tokens": 2})),
        fu.chat_json(
            "",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "gmail.search", "arguments": '{"query": "receipts"}'},
                }
            ],
        ),
        fu.status(503),
        fu.chat_json("after retry"),
        fu.status(401, leaky_error),
    )
    # A misconfigured harness that sends the *real* key: the proxy must not record headers.
    headers = {
        "X-Ratchet-Run-Id": "run_tsec",
        "X-Ratchet-Tier": "super",
        "X-Ratchet-Purpose": "agent",
        "Authorization": f"Bearer {fu.FAKE_KEY}",
    }
    body = {"messages": [{"role": "user", "content": "summarize my receipts"}]}
    async with env.client() as client:  # type: ignore[attr-defined]
        for stream in (False, True, False, False):  # the 4th is retried after a 503
            res = await client.post(CHAT, json={**body, "stream": stream}, headers=headers)
            assert res.status_code == 200
            if stream:
                await res.aread()
        res = await client.post(CHAT, json=body, headers=headers)
        assert res.status_code >= 400


async def test_proxy_run_leaves_no_secret_in_traces_costs_or_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = fu.make_env(tmp_path, **_secret_overrides())
    await _drive_proxy(env)
    assert env.fake.requests, "the fake upstream was never called"
    assert env.fake.requests[0].headers["authorization"] == f"Bearer {fu.FAKE_KEY}"

    captured = capsys.readouterr()
    logs = env.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "proxy.log").write_text(captured.out + captured.err, encoding="utf-8")
    assert _file_count(env.store.trace_dir("run_tsec")) > 0, "no trace was written"

    findings = _scan(env.data_dir)
    assert not findings, "\n".join(f.render() for f in findings)  # type: ignore[attr-defined]


async def test_proxy_never_echoes_the_upstream_key_to_the_harness(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, **_secret_overrides())
    leaky = {"error": {"message": f"invalid api key {fu.FAKE_KEY}", "type": "auth"}}
    env.fake.push(fu.status(401, leaky))
    headers = {"X-Ratchet-Run-Id": "run_echo", "X-Ratchet-Tier": "nano"}
    body = {"messages": [{"role": "user", "content": "hi"}]}
    async with env.client() as client:
        res = await client.post(CHAT, json=body, headers=headers)
    assert res.status_code == 401
    assert fu.FAKE_KEY not in res.text


def test_mock_tools_record_run_leaves_no_secret_in_cassettes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ratchet.mocktools.app import create_app
    from ratchet.mocktools.world import World
    from ratchet.pii import PiiTokenizer
    from ratchet.store import Store

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        ratchet_data_dir=tmp_path / "data",
        nebius_token_factory_key=SecretStr(fu.FAKE_KEY),
        **_secret_overrides(),
    )
    store = Store.open(settings.ratchet_data_dir)
    pii = PiiTokenizer(tmp_path / "data" / "pii.map.enc", tmp_path / "pii.key")
    world = World.load(ROOT / "tests" / "fixtures" / "world" / "sample")
    app = create_app(
        settings, store, mode="record", world=world, tokenizer=pii.tokenize_obj, enable_mcp=False
    )
    client = TestClient(app)
    body = f"use api_key={fu.FAKE_KEY} and {FAKE_SK} and {FAKE_TAVILY} to sync"
    calls: list[tuple[str, dict[str, object]]] = [
        ("gmail.search", {"query": f"token: {FAKE_SK}"}),
        ("gmail.send", {"to": ["bob@example.com"], "subject": "keys", "body": body}),
        ("github.list_issues", {"repo": "acme/widgets", "state": "all"}),
    ]
    for tool, args in calls:
        res = client.post(
            f"/tools/{tool}", json={"args": args}, headers={"X-Ratchet-Run-Id": "run_tsec_tools"}
        )
        assert res.status_code == 200, res.text
    captured = capsys.readouterr()
    (settings.ratchet_data_dir / "mocktools.log").write_text(captured.out + captured.err)
    assert any(store.cassettes_dir.rglob("*.json")), "no cassette was written"

    scanner = ss.Scanner([*SECRETS, FAKE_SK])
    findings = scanner.scan_paths([settings.ratchet_data_dir])
    assert not findings, "\n".join(f.render() for f in findings)


def _git_available() -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--git-dir"], check=True, capture_output=True
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


@pytest.mark.skipif(not _git_available(), reason="not a git checkout")
def test_git_tree_has_no_secrets() -> None:
    """Tracked plus untracked-but-not-ignored files; real values come from the env in CI."""
    files = ss.git_files(ROOT, untracked=True)
    assert files, "git ls-files returned nothing"
    scanner = ss.Scanner(
        ss.values_from_env(), allow_globs=ss.load_allowlist(ss.DEFAULT_ALLOWLIST), root=ROOT
    )
    findings = scanner.scan_paths(files)
    assert not findings, "\n".join(f.render() for f in findings)


def test_deploy_and_images_bake_in_no_secrets() -> None:
    """Dockerfiles, compose and deploy/ never assign a secret."""
    targets = [ROOT / "deploy", ROOT / "worker" / "Dockerfile", ROOT / ".github"]
    targets += [p for p in (ROOT / "Dockerfile", ROOT / "compose.yaml") if p.exists()]
    findings = ss.Scanner([]).scan_paths([t for t in targets if t.exists()])
    assert not findings, "\n".join(f.render() for f in findings)
    for dockerfile in [ROOT / "worker" / "Dockerfile", ROOT / "deploy" / "control.Dockerfile"]:
        if not dockerfile.exists():
            continue
        for line in dockerfile.read_text().splitlines():
            words = line.split()
            if words and words[0] in {"ENV", "ARG"}:
                assert not any(var in line for var in ss.SECRET_VARS), (dockerfile, line)


# --- the scanner itself (so a green secrets check is never vacuous) ---------------------------
def test_scanner_finds_configured_values_and_patterns_and_never_echoes_them(tmp_path: Path) -> None:
    import zstandard

    (tmp_path / "nested").mkdir()
    (tmp_path / "trace.jsonl").write_text(json.dumps({"auth": fu.FAKE_KEY}) + "\n")
    (tmp_path / "nested" / "log.txt").write_text(f"x\ny={FAKE_SK}\n")
    blob = zstandard.ZstdCompressor().compress(f"payload {FAKE_TAVILY}".encode())
    (tmp_path / "workspace.tar.zst").write_bytes(blob)
    (tmp_path / "db.sqlite").write_bytes(b"SQLite format 3\x00" + FAKE_ACTION.encode() + b"\x00")
    (tmp_path / "env.txt").write_text(f"NEBIUS_API_KEY={'R' * 6}w{'9' * 10}\n")
    findings = ss.Scanner([fu.FAKE_KEY, FAKE_ACTION], root=tmp_path).scan_paths([tmp_path])
    rules = {(f.path, f.rule) for f in findings}
    assert rules == {
        ("trace.jsonl", "configured_secret"),
        ("nested/log.txt", "openai_style_key"),
        ("workspace.tar.zst", "tavily_key"),
        ("db.sqlite", "configured_secret"),
        ("env.txt", "secret_assignment"),
    }
    rendered = "\n".join(f.render() for f in findings)
    for secret in (fu.FAKE_KEY, FAKE_SK, FAKE_TAVILY, FAKE_ACTION):
        assert secret not in rendered


def test_scanner_allowlist_exempts_patterns_but_never_configured_values(tmp_path: Path) -> None:
    corpus = tmp_path / "tests" / "fixtures" / "corpus.txt"
    corpus.parent.mkdir(parents=True)
    corpus.write_text(f"{FAKE_SK}\n{fu.FAKE_KEY}\n")
    scanner = ss.Scanner([fu.FAKE_KEY], allow_globs=["tests/fixtures/*.txt"], root=tmp_path)
    assert [f.rule for f in scanner.scan_paths([tmp_path])] == ["configured_secret"]


def test_scanner_ignores_placeholders_and_references(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "NEBIUS_TOKEN_FACTORY_KEY=\nTAVILY_API_KEY=tvly-...\nRATCHET_ACTION_TOKEN_SECRET=change-me\n"
        "NEBIUS_API_KEY=${{ secrets.NEBIUS_API_KEY }}\nNEBIUS_API_KEY=$NEBIUS_API_KEY\n"
        "NEBIUS_TOKEN_FACTORY_KEY: _secret(settings.nebius_token_factory_key)\n"
        '"NEBIUS_API_KEY": s.nebius_api_key,\nTAVILY_API_KEY = settings.tavily_api_key\n'
    )
    assert ss.Scanner(["change-me", ""]).scan_paths([tmp_path]) == []


def test_scanner_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "a.txt").write_text("nothing to see\n")
    assert ss.main([str(clean), "--no-allow"]) == 0
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "b.txt").write_text(f"leak {fu.FAKE_KEY}\n")
    assert ss.main([str(dirty), "--value", fu.FAKE_KEY, "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "dirty/b.txt:1: configured_secret" in out and fu.FAKE_KEY not in out
