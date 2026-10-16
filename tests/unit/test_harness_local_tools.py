"""Local tools: workspace confinement and the read-only shell allowlist."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ratchet.replay.harness.local_tools import (
    LocalTools,
    ToolRefused,
    check_shell_command,
    resolve_in_workspace,
)


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("alpha\nbeta\n")
    return root


@pytest.mark.parametrize("rel", ["../x", "/etc/passwd", "docs/../../x", "a\x00b"])
def test_resolve_refuses_escapes(ws, rel):
    with pytest.raises(ToolRefused):
        resolve_in_workspace(ws, rel)


def test_symlink_out_of_workspace_is_refused(ws, tmp_path):
    (tmp_path / "secret").write_text("s")
    os.symlink(tmp_path / "secret", ws / "link")
    tools = LocalTools(ws)
    assert tools.call("fs.read", {"path": "link"}) == {"error": "path escapes the workspace"}
    assert "escapes" in tools.call("shell.run", {"cmd": "cat link"})["error"]


def test_fs_round_trip(ws):
    tools = LocalTools(ws)
    assert tools.call("fs.write", {"path": "new/b.md", "content": "hi"})["ok"] is True
    assert tools.call("fs.read", {"path": "new/b.md"})["content"] == "hi"
    names = [e["name"] for e in tools.call("fs.list", {"path": "."})["entries"]]
    assert names == ["docs", "new"]
    assert tools.call("fs.delete", {"path": "new/b.md"})["ok"] is True
    assert tools.call("fs.delete", {"path": "docs"}) == {"error": "only files can be deleted"}
    assert tools.call("fs.read", {"path": "missing"}) == {"error": "no such file"}
    assert tools.call("fs.write", {"path": "x", "content": 3}) == {
        "error": "content must be a string"
    }


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf .",
        "cat docs/a.txt | sh",
        "ls > out.txt",
        "ls; rm x",
        "echo $(id)",
        "find . -delete",
        "find . -exec rm {} ;",
        "sort -o out.txt docs/a.txt",
        "sort --output=out.txt docs/a.txt",
        "cat /etc/passwd",
        "cat ../x",
        "python -c 1",
        "",
        # Known bypasses: options that execute code, write files or read outside.
        "sort --compress-program=sh -S 100K -T . docs/a.txt",
        "sort -o/abs/out docs/a.txt",
        "sort --outp=out.txt docs/a.txt",
        "grep -f/etc/passwd docs/a.txt",
        "grep --file=/etc/passwd docs/a.txt",
        "grep -r beta .",
        "diff --to-file=/etc/passwd docs/a.txt",
        "wc --files0-from=/etc/passwd",
        "find -L .",
        "uniq docs/a.txt docs/b.txt",
        "cut -f1 docs/a.txt",
        "head -n1 docs/a.txt",
        "head -c 5 docs/a.txt",
        "cat -",
        "cat",
        "ls -R",
        "grep -e beta docs/a.txt",
    ],
)
def test_shell_refuses_non_allowlisted_or_writing_commands(ws, cmd):
    with pytest.raises(ToolRefused):
        check_shell_command(cmd, ws)


def test_shell_runs_allowlisted_reads(ws):
    out = LocalTools(ws).call("shell.run", {"cmd": "grep -n beta docs/a.txt"})
    assert out["exit_code"] == 0 and out["stdout"].strip() == "2:beta"


@pytest.mark.parametrize("program", ["sort", "uniq", "diff", "find", "cut"])
def test_shell_allowlist_drops_commands_with_writing_or_exec_options(ws, program):
    with pytest.raises(ToolRefused, match="not allowed"):
        check_shell_command(f"{program} docs/a.txt", ws)


def test_shell_never_spawns_a_process(ws, monkeypatch):
    import subprocess

    def boom(*_a, **_k):
        raise AssertionError("shell.run spawned a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    tools = LocalTools(ws)
    assert tools.call("shell.run", {"cmd": "wc -l docs/a.txt"})["stdout"] == "2 docs/a.txt\n"
    assert tools.call("shell.run", {"cmd": "cat -n docs/a.txt"})["stdout"].startswith("     1\t")


@pytest.mark.parametrize(
    ("cmd", "stdout", "code"),
    [
        ("ls", "docs\n", 0),
        ("ls -la docs", "-        11 a.txt\n", 0),
        ("cat docs/a.txt", "alpha\nbeta\n", 0),
        ("head -n 1 docs/a.txt", "alpha\n", 0),
        ("tail -n 1 docs/a.txt", "beta\n", 0),
        ("wc -lw docs/a.txt", "2 2 docs/a.txt\n", 0),
        ("grep -c a docs/a.txt", "2\n", 0),
        ("grep -in BETA docs/a.txt", "2:beta\n", 0),
        ("grep -v alpha docs/a.txt", "beta\n", 0),
        ("grep -F a.p docs/a.txt", "", 1),
        ("grep ^b.t docs/a.txt", "beta\n", 0),
        ("grep (a) docs/a.txt", "", 1),
        ("cat docs/missing.txt", "", 1),
    ],
)
def test_shell_read_only_subset_matches_coreutils_output(ws, cmd, stdout, code):
    out = LocalTools(ws).call("shell.run", {"cmd": cmd})
    assert (out["stdout"], out["exit_code"]) == (stdout, code)


def test_shell_does_not_write_or_read_outside(ws, tmp_path):
    (tmp_path / "outside.txt").write_text("secret\n")
    tools = LocalTools(ws)
    for cmd in ["sort -o ../out docs/a.txt", "uniq docs/a.txt docs/b.txt", "cat ../outside.txt"]:
        assert "error" in tools.call("shell.run", {"cmd": cmd})
    assert not (ws / "docs" / "b.txt").exists()
    assert not (tmp_path / "out").exists()
