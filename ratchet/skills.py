"""Skill parsing helpers (SKILL.md format, agentskills.io specification).

A skill is a directory holding ``SKILL.md``: YAML frontmatter between ``---`` lines, then a
Markdown body. ``affected_skills`` and every other skill id in Ratchet is the *directory
name*. Validation follows agentskills.io: ``name`` is 1-64 chars of ``a-z0-9-`` without
leading, trailing or doubled hyphens and must match the directory; ``description`` is
1-1024 chars; ``compatibility`` is at most 500 chars. Client extras are tolerated:
Hermes nests a mapping under ``metadata.hermes`` and adds top-level ``version`` /
``platforms``, which the spec does not define — they are kept, not rejected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from ratchet.errors import SchemaError
from ratchet.log import get_logger

__all__ = [
    "SKILL_FILE",
    "SkillDoc",
    "split_frontmatter",
    "parse_skill_md",
    "validate_skill",
    "load_skills",
    "skill_dirs",
    "skill_for_path",
]

SKILL_FILE = "SKILL.md"
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_NAME = 64
_MAX_DESCRIPTION = 1024
_MAX_COMPATIBILITY = 500
_FENCE = "---"
_log = get_logger("skills")


@dataclass(frozen=True)
class SkillDoc:
    name: str  # frontmatter ``name`` (falls back to the directory name when absent)
    description: str
    body: str
    path: Path | None  # the SKILL.md file, when loaded from disk
    metadata: Mapping[str, object] = field(default_factory=dict)  # frontmatter ``metadata``
    frontmatter: Mapping[str, object] = field(default_factory=dict)  # every frontmatter key

    @property
    def dir_name(self) -> str | None:
        return self.path.parent.name if self.path is not None else None


def _invalid(message: str, path: Path | None) -> SchemaError:
    return SchemaError(message, code="skill_invalid", context={"path": str(path) if path else None})


def split_frontmatter(text: str, *, path: Path | None = None) -> tuple[dict[str, object], str]:
    """Return ``(frontmatter, body)``; raises ``SchemaError(skill_invalid)`` when malformed."""
    text = text.lstrip("﻿")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FENCE:
        raise _invalid("SKILL.md has no YAML frontmatter", path)
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            raw = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            break
    else:
        raise _invalid("SKILL.md frontmatter is not closed with ---", path)
    try:
        loaded = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise _invalid(
            f"SKILL.md frontmatter is not valid YAML ({type(exc).__name__})", path
        ) from exc
    if not isinstance(loaded, dict):
        raise _invalid("SKILL.md frontmatter must be a mapping", path)
    return {str(k): v for k, v in loaded.items()}, body.lstrip("\n")


def parse_skill_md(text: str, *, path: Path | None = None) -> SkillDoc:
    """Parse one SKILL.md. Structure errors raise; constraint violations do not (see validate)."""
    front, body = split_frontmatter(text, path=path)
    raw_name = front.get("name")
    name = str(raw_name).strip() if raw_name is not None else ""
    if not name and path is not None:
        name = path.parent.name
    raw_desc = front.get("description")
    description = " ".join(str(raw_desc).split()) if raw_desc is not None else ""
    meta = front.get("metadata")
    metadata: dict[str, object] = (
        {str(k): v for k, v in meta.items()} if isinstance(meta, dict) else {}
    )
    return SkillDoc(name, description, body, path, metadata, front)


def validate_skill(doc: SkillDoc, *, dir_name: str | None = None) -> list[str]:
    """agentskills.io constraint violations as short problem strings (empty = valid)."""
    problems: list[str] = []
    name = str(doc.frontmatter.get("name") or "").strip()
    if not name:
        problems.append("name is required")
    elif len(name) > _MAX_NAME:
        problems.append(f"name is longer than {_MAX_NAME} characters")
    elif not _NAME_RE.match(name):
        problems.append("name must be lowercase letters, digits and single hyphens")
    expected_dir = dir_name if dir_name is not None else doc.dir_name
    if name and expected_dir is not None and name != expected_dir:
        problems.append(f"name {name!r} does not match directory {expected_dir!r}")
    if not doc.description:
        problems.append("description is required")
    elif len(doc.description) > _MAX_DESCRIPTION:
        problems.append(f"description is longer than {_MAX_DESCRIPTION} characters")
    compatibility = doc.frontmatter.get("compatibility")
    if compatibility is not None and len(str(compatibility)) > _MAX_COMPATIBILITY:
        problems.append(f"compatibility is longer than {_MAX_COMPATIBILITY} characters")
    return problems


def load_skills(root: Path, *, strict: bool = False) -> list[SkillDoc]:
    """Every skill under ``root`` (any depth: Hermes nests category directories).

    Non-strict (the default) skips unparseable files and keeps skills that only violate
    naming constraints, logging both; strict raises ``SchemaError(skill_invalid)``.
    """
    docs: list[SkillDoc] = []
    if not root.is_dir():
        return docs
    for path in sorted(root.rglob(SKILL_FILE)):
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts[:-1]) or not path.is_file():
            continue
        try:
            doc = parse_skill_md(path.read_text(encoding="utf-8"), path=path)
        except (SchemaError, UnicodeDecodeError) as exc:
            if strict:
                raise _invalid(f"{rel.as_posix()}: {exc}", path) from exc
            _log.warning("skills.unparseable", path=rel.as_posix())
            continue
        problems = validate_skill(doc)
        if problems:
            if strict:
                raise _invalid(f"{rel.as_posix()}: {'; '.join(problems)}", path)
            _log.warning("skills.invalid", path=rel.as_posix(), n_problems=len(problems))
        docs.append(doc)
    return sorted(docs, key=lambda d: (d.dir_name or d.name, str(d.path)))


def skill_dirs(files: Iterable[str]) -> dict[str, str]:
    """``{skill id: relative dir}`` from a repository file listing (dirs holding SKILL.md).

    Skill ids are directory basenames. If two nested directories share a basename, the
    shallowest (then lexicographically first) wins.
    """
    dirs = sorted(
        (
            PurePosixPath(f).parent
            for f in files
            if PurePosixPath(f).name == SKILL_FILE and PurePosixPath(f).parent.name
        ),
        key=lambda p: (len(p.parts), p.as_posix()),
    )
    found: dict[str, str] = {}
    for directory in dirs:
        found.setdefault(directory.name, directory.as_posix())
    return found


def skill_for_path(path: str, dirs: Mapping[str, str]) -> str | None:
    """The skill id owning ``path`` (deepest skill directory containing it), or ``None``
    for files outside every skill (repo-root files, shared non-skill directories)."""
    parts = PurePosixPath(path).parts
    best: tuple[int, str] | None = None
    for skill, rel in dirs.items():
        rel_parts = PurePosixPath(rel).parts
        inside = parts[: len(rel_parts)] == rel_parts and len(parts) > len(rel_parts)
        if inside and (best is None or len(rel_parts) > best[0]):
            best = (len(rel_parts), skill)
    return best[1] if best is not None else None
