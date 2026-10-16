"""Shared helpers in ``ratchet.util``."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_copy_tree_contents_copies_files_without_touching_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Jobs output volume refuses chmod/utime; the copy must never attempt them."""
    import shutil

    from ratchet.util import copy_tree_contents

    src = tmp_path / "src"
    (src / "steps" / "deep").mkdir(parents=True)
    (src / "run.json").write_text("{}")
    (src / "steps" / "deep" / "s1.json").write_text("1")
    (src / "link").symlink_to(src / "run.json")

    def refuse(*_a: object, **_k: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(shutil, "copystat", refuse)
    monkeypatch.setattr(shutil, "copymode", refuse)
    monkeypatch.setattr("os.chmod", refuse)
    monkeypatch.setattr("os.utime", refuse)
    dst = tmp_path / "mount" / "out"
    assert copy_tree_contents(src, dst) == 2
    assert (dst / "run.json").read_text() == "{}"
    assert (dst / "steps" / "deep" / "s1.json").read_text() == "1"
    assert not (dst / "link").exists()
    assert copy_tree_contents(src, dst) == 2  # idempotent over an existing tree
