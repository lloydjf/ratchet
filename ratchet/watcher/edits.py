"""Build a :class:`~ratchet.models.SkillEdit` from a candidate commit.

Used by the watcher for detected edits and by the orchestrator for seeded (Simulate)
edits made through :meth:`SkillsRepo.apply_files`, so both produce identical records.
"""

from __future__ import annotations

from ratchet import clock
from ratchet.models import SkillEdit
from ratchet.util import new_id
from ratchet.watcher.gitops import AuthorKind, SkillsRepo

__all__ = ["build_edit", "summarize"]

_MINUS = "−"  # typographic minus, as in the UI ("+3 −1")


def _relative_to_skill(path: str, skill: str) -> str:
    parts = path.split("/")
    if skill in parts[:-1]:
        return "/".join(parts[parts.index(skill) + 1 :])
    return path


def summarize(stats: dict[str, tuple[int, int]], affected: list[str]) -> str:
    """One-liner "what changed": ``github-triage: SKILL.md changed (+3 −1)``."""
    added = sum(a for a, _ in stats.values())
    removed = sum(r for _, r in stats.values())
    counts = f"(+{added} {_MINUS}{removed})"
    subject = ", ".join(affected) if affected else "skills repo"
    if len(stats) == 1:
        (path,) = stats
        name = _relative_to_skill(path, affected[0]) if len(affected) == 1 else path
        return f"{subject}: {name} changed {counts}"
    return f"{subject}: {len(stats)} files changed {counts}"


def build_edit(
    repo: SkillsRepo,
    *,
    commit: str,
    parent: str | None = None,
    author: AuthorKind,
    session_hash: str | None = None,
    edit_id: str | None = None,
) -> tuple[SkillEdit, str]:
    """``(SkillEdit, diff_text)`` for ``parent..commit``; ``parent`` defaults to main's head.

    ``diff_ref`` is left empty: ``Store.save_edit`` writes the diff and fills it in.
    """
    base = parent or repo.main_head()
    diff_text = repo.diff(base, commit)
    affected = repo.affected_skills(base, commit)
    edit = SkillEdit(
        edit_id=edit_id or new_id("edit"),
        commit=commit,
        parent=base,
        files=repo.changed_files(base, commit),
        diff_ref="",
        affected_skills=affected,
        author=author,
        detected_at=clock.now(),
        summary=summarize(repo.numstat(base, commit), affected),
        session_hash=session_hash,
    )
    return edit, diff_text
