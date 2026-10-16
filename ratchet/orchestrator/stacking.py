"""Stacked edits: never let one merge ship an earlier edit that is still held.

Edits accumulate on ``candidate``. ``merge_candidate(commit=X)`` fast-forwards ``main`` to X,
which also lands every commit between ``main`` and X. If one of those belongs to an edit
that is still held (WARN) or still being judged, merging X would deploy it without a
verdict. A reverted edit is safe to carry only when its revert commit (the verdict's
``action_commit``) also lands — is an ancestor of the merge target — so the net change is
zero. A verdict that says ``reverted`` but whose revert has not run yet (the
``deciding`` → ``acting`` window) or sits after the target is unresolved (H1).
"""

from __future__ import annotations

from ratchet.errors import NotFoundError
from ratchet.models import SkillEdit
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.store import Store
from ratchet.watcher.gitops import SkillsRepo

__all__ = ["unresolved_ancestors", "merge_target", "STACKED_ON_HELD"]

STACKED_ON_HELD = "stacked_on_held"


def unresolved_ancestors(
    store: Store, repo: SkillsRepo, edit: SkillEdit, *, target: str | None = None
) -> list[str]:
    """Edit ids whose commits would ride along with ``edit``'s merge without a verdict.

    ``target`` is the commit main would fast-forward to (default ``edit.commit``).
    """
    tip = repo.resolve(target or edit.commit)
    commits = set(repo.commits_between(repo.main_head(), tip)) - {repo.resolve(edit.commit)}
    if not commits:
        return []
    marks = ",".join("?" for _ in commits)
    rows = store.query(
        f"""SELECT edit_id FROM edits WHERE "commit" IN ({marks}) AND edit_id != ?
            AND session_hash IS ? ORDER BY detected_at""",
        (*sorted(commits), edit.edit_id, edit.session_hash),
    )
    return [str(r["edit_id"]) for r in rows if not _resolved(store, repo, str(r["edit_id"]), tip)]


def _resolved(store: Store, repo: SkillsRepo, edit_id: str, tip: str) -> bool:
    """True only for a reverted edit whose revert commit is carried by the merge too."""
    try:
        verdict = store.load_verdict(verdict_id_for(edit_id))
    except NotFoundError:
        return False  # still being judged
    if verdict.action != "reverted" or not verdict.action_commit:
        return False  # held, or decided-reverted but the revert has not run yet
    return repo.is_ancestor(verdict.action_commit, tip)


def merge_target(store: Store, repo: SkillsRepo, edit: SkillEdit) -> str | None:
    """The commit to fast-forward main to so ``edit`` ships alone, or ``None`` if blocked.

    ``edit.commit`` when nothing unresolved sits below it. When the only obstacles are
    reverted edits whose revert commits sit *above* it on candidate, merging candidate's
    head carries those reverts too (net zero) — provided every edit up to that head is
    resolved.
    """
    if not unresolved_ancestors(store, repo, edit):
        return repo.resolve(edit.commit)
    head = repo.candidate_head()
    if repo.is_ancestor(edit.commit, head) and not unresolved_ancestors(
        store, repo, edit, target=head
    ):
        return head
    return None
