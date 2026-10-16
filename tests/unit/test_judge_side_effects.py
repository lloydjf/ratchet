"""The side-effect rule table."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import FakeModelClient, settings  # noqa: E402

from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.judge import side_effects  # noqa: E402
from ratchet.store import Store  # noqa: E402
from ratchet.tools.catalog import CATALOG  # noqa: E402

SIDE_EFFECT_TABLE = [
    ("github.get", {}, "read"),
    ("sheets.list", {}, "read"),
    ("gmail.search", {}, "read"),
    ("fs.read", {}, "read"),
    ("shell.run", {"cmd": "ls -la | grep csv"}, "read"),
    ("fs.write", {}, "write_reversible"),
    ("git.commit", {}, "write_reversible"),
    ("github.close_issue", {}, "write_irreversible"),
    ("github.merge_pr", {}, "write_irreversible"),
    ("calendar.create", {}, "write_irreversible"),
    ("fs.delete", {}, "destructive"),
    ("shell.run", {"cmd": "rm -rf data/"}, "destructive"),
    ("db.drop", {}, "destructive"),
    ("gmail.send", {}, "external_send"),
    ("slack.post", {}, "external_send"),
    ("sms.send", {}, "external_send"),
    ("stripe.charge", {}, "spend"),
    ("shop.purchase", {}, "spend"),
    ("payments.pay", {}, "spend"),
    ("vendor.pay", {}, "spend"),
]


@pytest.mark.parametrize(("tool", "args", "expected"), SIDE_EFFECT_TABLE)
def test_side_effect_table_maps_every_tool_to_its_class(
    tool: str, args: dict, expected: str
) -> None:
    assert side_effects.classify_rule(tool, args) == expected


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("cat a.csv | sort | uniq", "read"),
        ("git status && git log -3", "read"),
        ("LC_ALL=C sort data.csv", "read"),
        ("python clean.py", "write_reversible"),
        ("cat a > b", "destructive"),
        ("echo x >> log.txt", "destructive"),
        ("ls > /dev/null", "read"),
        ("find . -name '*.tmp' -delete", "destructive"),
        ("find . -type f -exec rm {} +", "destructive"),
        ("find . -execdir cat {} ;", "destructive"),
        ("find . -name x", "read"),
        ("sort --compress-program=sh data.csv", "destructive"),
        ("sort -o out.csv data.csv", "destructive"),
        ("ls 2>&1", "read"),
        ("git push --force origin main", "destructive"),
        ("find . -name x; rm x", "destructive"),
        ("", "write_reversible"),
    ],
)
def test_shell_run_is_classified_from_its_command(cmd: str, expected: str) -> None:
    assert side_effects.classify_rule("shell.run", {"cmd": cmd}) == expected


def test_catalog_tools_agree_with_the_rule_table() -> None:
    for spec in CATALOG.values() if isinstance(CATALOG, dict) else CATALOG:
        ruled = side_effects.classify_rule(spec.name, {"cmd": "ls"})
        assert ruled == spec.side_effect, spec.name


def test_unknown_tool_has_no_rule() -> None:
    assert side_effects.classify_rule("crm.update_contact", {}) is None


async def test_unknown_tool_falls_to_nano_and_is_cached(tmp_path: Path) -> None:
    with settings(tmp_path):
        store = Store.open(tmp_path / "data")
        client = FakeModelClient().add(
            "classify_side_effect", '{"side_effect": "write_reversible", "reason": "edits a row"}'
        )
        first = await side_effects.classify("crm.update_contact", {"id": 3}, client, store)
        second = await side_effects.classify("crm.update_contact", {"id": 4}, client, store)
    assert first == ("write_reversible", "edits a row") == second
    assert len(client.calls) == 1 and client.calls[0].tier == "nano"
    assert "crm.update_contact" in client.calls[0].user_text


async def test_ruled_tools_never_call_nano(tmp_path: Path) -> None:
    with settings(tmp_path):
        store = Store.open(tmp_path / "data")
        client = FakeModelClient()
        assert await side_effects.classify("gmail.send", {}, client, store) == (
            "external_send",
            "rule",
        )
    assert client.calls == []


async def test_nano_failure_is_treated_as_consequential_and_not_cached(tmp_path: Path) -> None:
    with settings(tmp_path):
        store = Store.open(tmp_path / "data")
        client = FakeModelClient().add("classify_side_effect", UpstreamError("down"))
        cls, reason = await side_effects.classify("crm.merge", {}, client, store)
        assert cls == "write_irreversible" and reason == "upstream_unavailable"
        assert store.side_effect_cached("crm.merge") is None


def test_bad_rule_file_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "rules.yaml"
    bad.write_text(
        "rules:\n  - {pattern: 'x.*', class: harmless}\nshell: {tool: shell.run, read_only: [],"
        " destructive: [], otherwise: read}\n"
    )
    with pytest.raises(ValueError, match="harmless"):
        side_effects.load_rules(bad)
