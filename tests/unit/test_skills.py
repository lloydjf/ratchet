"""SKILL.md parsing and agentskills.io validation, tolerant of Hermes extras."""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.errors import SchemaError
from ratchet.skills import (
    load_skills,
    parse_skill_md,
    skill_dirs,
    skill_for_path,
    split_frontmatter,
    validate_skill,
)

HERMES = """---
name: github-triage
description: >
  Triage GitHub issues, label them by area
  and close obvious duplicates.
version: 1.2.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [github, issues]
    category: dev
---

# GitHub triage

Steps.
"""


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_hermes_skill_keeps_extras() -> None:
    doc = parse_skill_md(HERMES)
    assert doc.name == "github-triage"
    assert (
        doc.description == "Triage GitHub issues, label them by area and close obvious duplicates."
    )
    assert doc.body.startswith("# GitHub triage")
    assert doc.metadata == {"hermes": {"tags": ["github", "issues"], "category": "dev"}}
    assert doc.frontmatter["version"] == "1.2.0"
    assert validate_skill(doc, dir_name="github-triage") == []


@pytest.mark.parametrize(
    "text",
    ["no frontmatter", "---\nname: x\n", "---\n: : bad: [\n---\n", "---\n- a list\n---\n"],
)
def test_malformed_frontmatter_raises_skill_invalid(text: str) -> None:
    with pytest.raises(SchemaError) as info:
        split_frontmatter(text)
    assert info.value.code == "skill_invalid"


def test_bom_and_empty_frontmatter() -> None:
    front, body = split_frontmatter("﻿---\n---\nbody\n")
    assert front == {} and body == "body\n"


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("", "name is required"),
        ("x" * 65, "longer than 64"),
        ("GitHub-Triage", "lowercase"),
        ("-triage", "lowercase"),
        ("triage-", "lowercase"),
        ("git--triage", "lowercase"),
        ("other-name", "does not match directory"),
    ],
)
def test_name_constraints(name: str, problem: str) -> None:
    doc = parse_skill_md(f"---\nname: '{name}'\ndescription: ok\n---\n")
    problems = validate_skill(doc, dir_name="other" if name == "other-name" else name or "x")
    assert any(problem in p for p in problems), problems


def test_description_and_compatibility_limits() -> None:
    long_desc = parse_skill_md(f"---\nname: a\ndescription: {'d' * 1025}\n---\n")
    assert any("description is longer" in p for p in validate_skill(long_desc, dir_name="a"))
    missing = parse_skill_md("---\nname: a\n---\n")
    assert "description is required" in validate_skill(missing, dir_name="a")
    compat = parse_skill_md(f"---\nname: a\ndescription: d\ncompatibility: {'c' * 501}\n---\n")
    assert any("compatibility" in p for p in validate_skill(compat, dir_name="a"))
    exactly = parse_skill_md(f"---\nname: {'a' * 64}\ndescription: {'d' * 1024}\n---\n")
    assert validate_skill(exactly, dir_name="a" * 64) == []


def test_load_skills_is_recursive_and_tolerant(tmp_path: Path) -> None:
    write(tmp_path, "github-triage/SKILL.md", HERMES)
    write(tmp_path, "dev/deploy-check/SKILL.md", "---\nname: deploy-check\ndescription: d\n---\n")
    write(tmp_path, "Bad_Name/SKILL.md", "---\nname: Bad_Name\ndescription: d\n---\n")
    write(tmp_path, "broken/SKILL.md", "no frontmatter here")
    write(tmp_path, ".hub/cache/SKILL.md", "---\nname: cache\ndescription: d\n---\n")
    write(tmp_path, "unnamed/SKILL.md", "---\ndescription: falls back to dir\n---\n")
    docs = load_skills(tmp_path)
    assert [d.dir_name for d in docs] == ["Bad_Name", "deploy-check", "github-triage", "unnamed"]
    assert docs[-1].name == "unnamed"
    assert load_skills(tmp_path / "missing") == []


def test_load_skills_strict_raises(tmp_path: Path) -> None:
    write(tmp_path, "Bad_Name/SKILL.md", "---\nname: Bad_Name\ndescription: d\n---\n")
    with pytest.raises(SchemaError):
        load_skills(tmp_path, strict=True)
    write(tmp_path / "b", "broken/SKILL.md", "nope")
    with pytest.raises(SchemaError):
        load_skills(tmp_path / "b", strict=True)


def test_skill_dirs_and_path_ownership() -> None:
    files = [
        "github-triage/SKILL.md",
        "github-triage/scripts/x.py",
        "dev/deploy-check/SKILL.md",
        "other/deploy-check/SKILL.md",
        "README.md",
        "SKILL.md",
    ]
    dirs = skill_dirs(files)
    assert dirs == {"github-triage": "github-triage", "deploy-check": "dev/deploy-check"}
    assert skill_for_path("github-triage/scripts/x.py", dirs) == "github-triage"
    assert skill_for_path("dev/deploy-check/SKILL.md", dirs) == "deploy-check"
    assert skill_for_path("README.md", dirs) is None
    assert skill_for_path("shared/github-triage.md", dirs) is None
