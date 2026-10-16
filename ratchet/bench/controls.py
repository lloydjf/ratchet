"""Control cases (dataset construction, step 3) and the Tavily fixtures.

**N0** (2 per skill, deterministic): cosmetic edits of the SKILL.md *body* only — the
frontmatter (name and the trigger ``description``) is never touched, so routing cannot move.
Variant 1 adds a neutral HTML comment under the title and normalizes trailing whitespace;
variant 2 rewords stock phrases (``Use this skill when`` → ``Use this skill whenever``, …) and
retitles ``## Steps``. Same input, same output.

**D1** (2 per world-dependent skill): the skill text is unchanged except for one added blank
line (the commit that triggers the check); the *world* moves. Each case is a world-file
override (one volatile value changed) that ``orchestrator.overlay`` turns into an overlay
cassette set for the goldens, plus Tavily fixtures that report the new value for every
golden fact carrying the old one.

**Base Tavily fixtures** report each golden fact's *recorded* value, so the drift check on a
real regression of a world-dependent skill finds the world unchanged.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ratchet.judge.drift import current_query
from ratchet.models import Trace, WorldFact
from ratchet.util import sha256_hex

__all__ = [
    "N0_VARIANTS",
    "cosmetic",
    "D1Spec",
    "D1_SPECS",
    "d1_trigger",
    "apply_override",
    "world_override",
    "values_match",
    "fixture_payload",
    "fact_fixtures",
    "write_fixtures",
]

N0_VARIANTS = 2
_FRONTMATTER = re.compile(r"\A(---\n.*?\n---\n)(.*)\Z", re.DOTALL)
_TITLE = re.compile(r"^(# [^\n]+\n)", re.MULTILINE)
_WORDING: tuple[tuple[str, str], ...] = (
    ("Use this skill when", "Use this skill whenever"),
    ("## Steps", "## Procedure"),
    ("in the order the task names them", "in the same order as the task"),
    ("each item on its own line", "with each item on a separate line"),
)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _split(skill_md: str) -> tuple[str, str]:
    match = _FRONTMATTER.match(skill_md)
    if match is None:
        return "", skill_md
    return match.group(1), match.group(2)


def cosmetic(skill_md: str, variant: int) -> tuple[str, str]:
    """``(new SKILL.md, change summary)`` for N0 variant 1 or 2 (deterministic)."""
    front, body = _split(skill_md)
    if variant == 1:
        lines = [line.rstrip() for line in body.splitlines()]
        text = "\n" + "\n".join(lines).strip("\n") + "\n"
        note = "<!-- Reviewed for clarity; the steps are unchanged. -->\n"
        new = _TITLE.sub(lambda m: m.group(1) + "\n" + note, text, count=1)
        if new == text:
            new = note + "\n" + text
        return front + new, "Added a reviewer comment under the title and trimmed whitespace."
    if variant == 2:
        new = body
        for old, repl in _WORDING:
            new = new.replace(old, repl, 1)
        if new == body:
            new = body.rstrip("\n") + "\n\nFollow the steps in order.\n"
        return front + new, "Reworded the introduction and retitled the steps section."
    raise ValueError(f"N0 variant must be 1..{N0_VARIANTS}")


# --- D1 ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class D1Spec:
    edit_id: str
    skill: str
    world_file: str  # e.g. "fx.json"
    path: tuple[str, ...]  # keys down to the leaf that moves
    old_value: str
    new_value: str
    summary: str


D1_SPECS: tuple[D1Spec, ...] = (
    D1Spec("d1-fx-expense-converter-1", "fx-expense-converter", "fx.json",
           ("rates", "EUR/USD", "2026-10-14"), "1.0800", "1.1630",
           "EUR/USD for 2026-10-14 moved from 1.0800 to 1.1630."),
    D1Spec("d1-fx-expense-converter-2", "fx-expense-converter", "fx.json",
           ("rates", "GBP/USD", "2026-10-13"), "1.2702", "1.3105",
           "GBP/USD for 2026-10-13 moved from 1.2702 to 1.3105."),
    D1Spec("d1-weekly-research-brief-1", "weekly-research-brief", "web.json",
           ("pages", "https://news.example.com/quilldb-4-2", "text"), "4.2", "4.3",
           "The QuillDB news page now reports release 4.3 instead of 4.2."),
    D1Spec("d1-weekly-research-brief-2", "weekly-research-brief", "web.json",
           ("pages", "https://news.example.com/orbit7-service", "text"), "$49", "$59",
           "The Orbit-7 service page now reports $59 per month instead of $49."),
    D1Spec("d1-dependency-updater-1", "dependency-updater", "web.json",
           ("pages", "https://pkgs.example.com/httpkit/changelog", "text"), "2.5.1", "2.6.0",
           "httpkit's changelog now lists 2.6.0 as the latest release instead of 2.5.1."),
    D1Spec("d1-dependency-updater-2", "dependency-updater", "web.json",
           ("pages", "https://pkgs.example.com/logmint/changelog", "text"), "0.12.4", "0.13.0",
           "logmint's changelog now lists 0.13.0 as the latest release instead of 0.12.4."),
)  # fmt: skip


def d1_trigger(skill_md: str) -> str:
    """The D1 commit: the skill text plus one trailing blank line (no wording change)."""
    return skill_md.rstrip("\n") + "\n\n"


def apply_override(data: object, path: tuple[str, ...], old: str, new: str) -> object:
    """A deep copy of ``data`` with the leaf at ``path`` moved from ``old`` to ``new``."""
    out = copy.deepcopy(data)
    node = out
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            raise KeyError("/".join(path))
        node = node[key]
    leaf = path[-1]
    if not isinstance(node, dict) or leaf not in node:
        raise KeyError("/".join(path))
    current = node[leaf]
    if isinstance(current, str):
        if old not in current:
            raise ValueError(f"{'/'.join(path)} does not contain {old!r}")
        node[leaf] = current.replace(old, new)
    else:
        node[leaf] = float(new)
    return out


def world_override(world_dir: Path, spec: D1Spec) -> dict[str, object]:
    """``{world file name: overridden content}`` for :func:`labels.write_edit`."""
    data = json.loads((world_dir / spec.world_file).read_text(encoding="utf-8"))
    return {spec.world_file: apply_override(data, spec.path, spec.old_value, spec.new_value)}


# --- Tavily fixtures --------------------------------------------------------------------------
def values_match(observed: str, expected: str) -> bool:
    """Same value, tolerating number formatting (``1.0800`` == ``1.08``, ``$49`` == ``49``)."""
    a, b = _NUMBER.findall(observed), _NUMBER.findall(expected)
    if a and b:
        return any(abs(float(x) - float(b[0])) < 1e-9 for x in a)
    return expected.strip().lower() in observed.strip().lower()


def fixture_payload(fact: WorldFact, value: str, *, as_of: str) -> dict[str, object]:
    """A synthetic Tavily search response stating ``value`` for ``fact``."""
    statement = fact.statement
    if fact.value and fact.value in statement:
        statement = statement.replace(fact.value, value)
    else:
        statement = f"{statement} Latest reported value: {value}."
    slug = sha256_hex(fact.query_hint)[:10]
    return {
        "_note": "Synthetic Tavily response for SkillRegress-Bench; not real data. "
        "Every URL is example.com.",
        "query": fact.query_hint,
        "results": [
            {
                "title": f"Latest figures ({as_of})",
                "url": f"https://facts.example.com/{slug}",
                "content": f"As of {as_of}: {statement}",
                "score": 0.9,
                "published_date": as_of,
            }
        ],
    }


def fact_fixtures(
    goldens: list[Trace], *, as_of: str, moved: D1Spec | None = None
) -> dict[str, dict[str, object]]:
    """``{query_hint: payload}`` for every volatile fact; ``moved`` reports its new value."""
    out: dict[str, dict[str, object]] = {}
    for golden in goldens:
        for fact in golden.world_facts:
            if not fact.query_hint or fact.query_hint in out:
                continue
            value = fact.value or fact.statement
            if moved is not None and values_match(value, moved.old_value):
                value = moved.new_value
            out[fact.query_hint] = fixture_payload(fact, value, as_of=as_of)
    return out


def write_fixtures(root: Path, fixtures: dict[str, dict[str, object]]) -> dict[str, object]:
    """Write ``<sha16>.json`` files plus ``index.json`` (query → file) into ``root``.

    The index maps both the recorded hint and the query the drift judge actually sends,
    ``current_query(hint)`` ("… today"), which resolves to the fixture of the latest recorded
    hint; the first live sweep indexed the hint alone and every bench drift check came back
    ``drift_unavailable``, the second mapped "today" to the oldest recording and no D1 drift
    was ever found.
    """
    root.mkdir(parents=True, exist_ok=True)
    index: dict[str, object] = {}
    for query, payload in sorted(fixtures.items()):
        name = f"{sha256_hex(query)[:16]}.json"
        (root / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        index[query] = name
        index.setdefault(current_query(query), name)
    (root / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index
