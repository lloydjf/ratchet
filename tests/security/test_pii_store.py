"""0 raw PII across the cassettes a record-mode run writes.

The mock tool server runs in record mode with the real ``ratchet.pii`` tokenizer against the
sample world, and every line of the fake PII corpus is pushed through side-effecting tools
(mail, Slack, GitHub comments) and read back through search. The whole cassettes dir, and
the ToolCall steps of the run's trace, are then scanned with ``scripts/secret_scan.py --pii``
plus an exact-literal check for every email, phone number and key in the corpus.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from ratchet.config.settings import Settings
from ratchet.mocktools.app import create_app
from ratchet.mocktools.world import World
from ratchet.models import ToolCall
from ratchet.pii import PiiTokenizer
from ratchet.store import Store

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "fixtures" / "pii_corpus.txt"
WORLD = ROOT / "tests" / "fixtures" / "world" / "sample"
RUN_ID = "run_pii_store"


def _scanner_module() -> ModuleType:
    name = "ratchet_secret_scan"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "secret_scan.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


ss = _scanner_module()


def _corpus_lines() -> list[str]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.startswith("#")]


def _corpus_literals() -> set[str]:
    """Every email, phone and key-shaped value in the corpus, found with the scanner's rules."""
    text = "\n".join(_corpus_lines())
    found = {m.group(0) for m in ss.EMAIL.finditer(text)}
    found |= {m.group(0) for m in ss.PHONE.finditer(text)}
    for _, regex in ss.PATTERNS:
        found |= {m.group(0) for m in regex.finditer(text)}
    return found


@pytest.fixture
def recorded(tmp_path: Path) -> tuple[Store, Path]:
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "data")  # type: ignore[call-arg]
    store = Store.open(settings.ratchet_data_dir)
    pii = PiiTokenizer(tmp_path / "data" / "pii.map.enc", tmp_path / "pii.key")
    app = create_app(
        settings,
        store,
        mode="record",
        world=World.load(WORLD),
        tokenizer=pii.tokenize_obj,
        enable_mcp=False,
    )
    client = TestClient(app)
    emails = sorted(m.group(0) for m in ss.EMAIL.finditer(CORPUS.read_text(encoding="utf-8")))

    def call(tool: str, args: dict[str, object]) -> None:
        res = client.post(
            f"/tools/{tool}", json={"args": args}, headers={"X-Ratchet-Run-Id": RUN_ID}
        )
        assert res.status_code == 200, (tool, res.text)

    for number, line in enumerate(_corpus_lines(), start=1):
        call("slack.post", {"channel": "#ops", "text": line})
        call("gmail.send", {"to": emails[:2], "cc": emails[2:], "subject": line, "body": line})
        call("github.comment", {"repo": "acme/widgets", "issue": 11, "body": f"{number}: {line}"})
    for email in emails:
        call("gmail.search", {"query": f"from:{email}"})
    call("gmail.search", {"query": "planning"})  # the world's own mailbox, with real-shaped data
    call("calendar.freebusy", {"attendees": emails, "start": "2026-10-15T08:00:00Z",
                               "end": "2026-10-15T18:00:00Z"})  # fmt: skip
    pii.save()  # what the default record-mode tokenizer does after each call
    return store, tmp_path / "pii.key"


def _assert_clean(findings: list[object]) -> None:
    assert not findings, "\n".join(f.render() for f in findings)  # type: ignore[attr-defined]


def test_cassettes_hold_no_raw_pii_or_secrets(recorded: tuple[Store, Path]) -> None:
    store, _ = recorded
    cassettes = sorted(store.cassettes_dir.rglob("*.json"))
    assert len(cassettes) >= 10, "the record run wrote too few cassettes to mean anything"
    _assert_clean(ss.Scanner(pii=True, root=store.cassettes_dir).scan_paths([store.cassettes_dir]))
    literals = _corpus_literals()
    assert len(literals) >= 10
    for path in cassettes:
        text = path.read_text(encoding="utf-8")
        leaked = sorted(lit for lit in literals if lit in text)
        assert not leaked, (path.name, [ss.redact(v) for v in leaked])


def test_tool_call_steps_store_only_tokenized_values(recorded: tuple[Store, Path]) -> None:
    store, _ = recorded
    steps = [s for s in store.collect_steps(RUN_ID) if isinstance(s, ToolCall)]
    assert len(steps) >= 10
    dumped = "\n".join(json.dumps(s.model_dump(mode="json")) for s in steps)
    _assert_clean(ss.Scanner(pii=True).scan_text("steps", dumped))
    assert "<EMAIL_" in dumped or "<PHONE_" in dumped, "nothing was tokenized: vacuous run"


def test_the_token_map_is_encrypted_and_its_key_lives_outside_the_data_dir(
    recorded: tuple[Store, Path],
) -> None:
    store, key_path = recorded
    data_dir = store.cassettes_dir.parent
    token_map = data_dir / "pii.map.enc"
    assert token_map.exists() and key_path.exists()
    assert data_dir not in key_path.parents
    raw = token_map.read_bytes()
    for literal in _corpus_literals():
        assert literal.encode() not in raw
    assert key_path.stat().st_mode & 0o077 == 0, "the PII key must be 0600"
