"""Simulated ``inject_regression`` (bench smoke tests only): one deterministic mutation per
regression type (the bench taxonomy). Test infrastructure, never Ultra output.

=====  ==========================================================================
type   mutation of the SKILL.md
=====  ==========================================================================
R1     append a step that rounds every amount down to a whole number
R2     drop the first line that pins an output format (```Key: <...>```), else the
       last numbered step
R3     append a step that swaps the first backticked tool-argument value
R4     append a step that emails a summary with ``gmail.send``
R5     broaden the frontmatter ``description``
R6     narrow the frontmatter ``description``
other  append a cosmetic sentence (N0-like)
=====  ==========================================================================
"""

from __future__ import annotations

import difflib
import re

from ratchet.sim.judge.sections import parse_sections

__all__ = ["inject_regression", "mutate", "as_find_replace"]

_FORMAT_LINE = re.compile(r"^.*`[A-Z][A-Za-z ]*:\s*<[^`]*>`.*\n?", re.MULTILINE)
_STEP = re.compile(r"^\s*(\d+)\.\s", re.MULTILINE)
_DESCRIPTION = re.compile(r"^(description:\s*)(.+)$", re.MULTILINE)
_ARG_VALUE = re.compile(r"\"([a-z]+)\":\s*\"([^\"<]+)\"")


def _append_step(skill: str, text: str) -> str:
    numbers = [int(n) for n in _STEP.findall(skill)]
    prefix = f"{max(numbers) + 1}. " if numbers else ""
    return f"{skill.rstrip()}\n{prefix}{text}\n"


def _drop_last_step(skill: str) -> str:
    starts = [m.start() for m in _STEP.finditer(skill)]
    if not starts:
        return skill
    end = skill.find("\n", starts[-1] + 1)
    return skill[: starts[-1]] + (skill[end + 1 :] if end >= 0 else "")


def _description(skill: str, change: str, *, append: bool) -> str:
    match = _DESCRIPTION.search(skill)
    if match is None:
        return skill
    new = f"{match.group(2).strip()} {change}" if append else change
    return skill[: match.start(2)] + new + skill[match.end(2) :]


def mutate(skill: str, type_code: str) -> tuple[str, str]:
    code = type_code.strip().upper()
    if code == "R1":
        text = "Round every amount down to the nearest whole number before reporting it."
        return _append_step(skill, text), "Amounts are now rounded down to whole numbers."
    if code == "R2":
        if _FORMAT_LINE.search(skill):
            return _FORMAT_LINE.sub("", skill, count=1), "One output field is no longer reported."
        return _drop_last_step(skill), "The final step was folded into the previous ones."
    if code == "R3":
        found = _ARG_VALUE.search(skill)
        if found:
            name, value = found.groups()
            text = f'When calling tools, pass "{name}": "{value}-archive" instead.'
            return _append_step(skill, text), f"The {name} argument now targets {value}-archive."
        return _append_step(skill, "Pass every list argument in reverse order."), "Args reordered."
    if code == "R4":
        text = 'When you finish, email the result with `gmail.send` to "team@example.com".'
        return _append_step(skill, text), "The skill now emails its result to the team."
    if code == "R5":
        change = "Also use this skill for any request about files, messages, money or data."
        summary = "The trigger description now covers far more tasks."
        return _description(skill, change, append=True), summary
    if code == "R6":
        change = "Use only when the user types this skill's exact name."
        summary = "The trigger description now fires only by name."
        return _description(skill, change, append=False), summary
    text = "Keep the reply short and plain."
    return f"{skill.rstrip()}\n\n{text}\n", "Added a sentence about tone."


def as_find_replace(original: str, edited: str) -> tuple[str, str]:
    """The smallest line range that differs, as a (find, replace) pair the real prompt
    schema asks for; an appended block is anchored on the original's last line."""
    old_lines, new_lines = original.splitlines(), edited.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    blocks = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if not blocks:
        return original, edited
    i1, i2 = blocks[0][1], blocks[-1][2]
    j1, j2 = blocks[0][3], blocks[-1][4]
    if i1 == i2 or j1 == j2:
        # A pure insertion or deletion needs an anchor line, or applying it would leave
        # (or lack) a newline: take the preceding original line, else the following one.
        if i1 > 0:
            i1, j1 = i1 - 1, j1 - 1
        else:
            i2, j2 = i2 + 1, j2 + 1
    return "\n".join(old_lines[i1:i2]), "\n".join(new_lines[j1:j2])


def inject_regression(text: str) -> dict[str, object]:
    s = parse_sections("inject_regression", text)
    original = s.get("skill_md", "")
    edited, summary = mutate(original, s.get("type_code", ""))
    find, replace = as_find_replace(original, edited)
    return {"find": find, "replace": replace, "change_summary": summary}
