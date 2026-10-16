"""Skill loading (agentskills.io format) and the MinimalRunner's trigger matcher.

A skill is ``<skills_dir>/<name>/SKILL.md`` with YAML frontmatter holding at least
``name`` and ``description``. Validation follows the
agentskills.io specification: ``name`` is 1–64 chars of ``a-z0-9-`` with no leading,
trailing or doubled hyphen and must equal the directory name; ``description`` is
1–1024 chars. Invalid skills are skipped and logged by directory name only.

OpenClaw (read-only compatibility): an OpenClaw workspace keeps the same format at
``<workspace>/skills/<name>/SKILL.md``; its frontmatter extras (``homepage``,
``user-invocable``, single-line JSON ``metadata.openclaw``) are ignored here and stripped
with the frontmatter. Point ``RATCHET_SKILLS_REPO`` at that ``skills/`` directory; Ratchet
reads it and never writes OpenClaw's config (fixture ``tests/fixtures/skills/openclaw-layout``,
``tests/integration/test_openclaw_layout.py``).

Trigger matching is a deliberately simple, documented stand-in for a harness's trigger
logic. The adapter interface specifies *embedding* cosine ≥ 0.6; MinimalRunner must run
offline inside the sandbox, so the default matcher is a local *lexical* cosine
(bag of lower-cased, stop-word-filtered, lightly stemmed tokens, skill name + description
vs. the task). Lexical cosine of a short task against a one-sentence description sits far
below embedding cosine for the same pair: on the fixture skills
(``tests/fixtures/skills``) relevant task/skill pairs score 0.28–0.63 and unrelated pairs
≤ 0.20 (one shared word such as "report"), so the calibrated default threshold is
:data:`LEXICAL_THRESHOLD` = 0.25. An embedding matcher
can be injected (``MinimalRunner(matcher=..., threshold=0.6)``).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from ratchet.log import get_logger

__all__ = [
    "Skill",
    "SkillMatcher",
    "LEXICAL_THRESHOLD",
    "MAX_INJECTED_SKILLS",
    "parse_skill",
    "load_skills",
    "lexical_matcher",
    "select_skills",
]

LEXICAL_THRESHOLD = 0.25
MAX_INJECTED_SKILLS = 3

_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = (
    "a an and are as at be by can do for from has have how i if in into is it its me my "
    "of on or our please so that the their them then there these this to use used uses "
    "using was we what when where which while who will with you your"
)
_STOP = frozenset(_STOP_WORDS.split())

_log = get_logger("replay.harness")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str  # SKILL.md without the frontmatter
    path: Path


# (task, skills) -> [(skill name, score)]; higher is more relevant.
SkillMatcher = Callable[[str, Sequence[Skill]], list[tuple[str, float]]]


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    if not text.startswith("---"):
        return None
    lines = text.splitlines(keepends=True)
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None


def parse_skill(skill_dir: Path) -> Skill | None:
    """Parse and validate one skill directory; ``None`` when it is not a valid skill."""
    path = skill_dir / "SKILL.md"
    if not path.is_file():
        return None
    parts = _split_frontmatter(path.read_text(encoding="utf-8"))
    if parts is None:
        return None
    try:
        meta = yaml.safe_load(parts[0])
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    description = description.strip()
    if not (1 <= len(name) <= 64 and _NAME.match(name) and name == skill_dir.name):
        return None
    if not 1 <= len(description) <= 1024:
        return None
    return Skill(name=name, description=description, body=parts[1].strip(), path=path)


def load_skills(skills_dir: Path) -> list[Skill]:
    """All valid skills under ``skills_dir``, sorted by name (deterministic prompts)."""
    if not skills_dir.is_dir():
        return []
    skills: list[Skill] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill = parse_skill(child)
        if skill is None:
            _log.warning("harness.skill.invalid", skill_dir=child.name)
            continue
        skills.append(skill)
    return skills


def _stem(token: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def _bag(text: str) -> Counter[str]:
    tokens = (_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1)
    return Counter(tokens)


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(count * b[token] for token, count in a.items())
    norm = math.sqrt(sum(c * c for c in a.values())) * math.sqrt(sum(c * c for c in b.values()))
    return dot / norm if norm else 0.0


def lexical_matcher(task: str, skills: Sequence[Skill]) -> list[tuple[str, float]]:
    """Lexical cosine between the task and each skill's ``name + description``."""
    task_bag = _bag(task)
    return [
        (s.name, _cosine(task_bag, _bag(s.name.replace("-", " ") + " " + s.description)))
        for s in skills
    ]


def select_skills(
    task: str,
    skills: Sequence[Skill],
    *,
    matcher: SkillMatcher = lexical_matcher,
    threshold: float = LEXICAL_THRESHOLD,
    limit: int = MAX_INJECTED_SKILLS,
) -> list[Skill]:
    """Up to ``limit`` skills scoring ≥ ``threshold``; best first, ties broken by name."""
    by_name = {s.name: s for s in skills}
    scored = [(score, name) for name, score in matcher(task, skills) if name in by_name]
    ranked = sorted((pair for pair in scored if pair[0] >= threshold), key=lambda p: (-p[0], p[1]))
    return [by_name[name] for _, name in ranked[:limit]]
