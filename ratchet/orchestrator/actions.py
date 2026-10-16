"""Manual verdict actions: Rollback and Approve, under a per-verdict lock.

Each action takes the verdict's SQLite lock (``BEGIN IMMEDIATE`` + ``acting`` flag; a
concurrent action gets ``ConflictError(action_locked)``), runs only once the edit's pipeline
has finished (``ConflictError(not_ready)`` while it is still deciding or acting), runs
its git operation on the repository the edit lives in (operator or Simulate session; a
recorded run has no repository, so only its record changes), records the resulting
commit and emits ``verdict.rolled_back`` / ``verdict.acted``.

- **rollback**: ``git revert`` the edit's commit on candidate. A held edit never reached
  main, so main's content is unchanged (``ROLLBACK_TOAST``). A merged edit must leave main
  too: when later unjudged commits keep main from taking the revert, the rollback is
  refused with ``ConflictError(stacked_on_held)`` and nothing changes. A
  verdict that is already reverted (BLOCK, or rolled back before) is
  ``ConflictError(already_reverted)``.
- **approve**: fast-forward main to the held edit's commit; a conflict is
  ``ConflictError(merge_conflict)``; Approve of a merged verdict is idempotent. Approve of
  a *reverted* verdict is "approve anyway" (override the block): the revert
  is itself reverted on candidate, re-applying the change, and that commit is merged under
  the same stacking guard.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from ratchet import clock, copy
from ratchet.config.settings import Settings
from ratchet.errors import ConflictError
from ratchet.models import SkillEdit, Verdict
from ratchet.orchestrator.context import load_options, operator_repo
from ratchet.orchestrator.journal import TERMINAL
from ratchet.orchestrator.stacking import STACKED_ON_HELD, merge_target, unresolved_ancestors
from ratchet.store import Store
from ratchet.watcher.gitops import SkillsRepo

__all__ = [
    "rollback",
    "approve",
    "repo_for_edit",
    "ALREADY_REVERTED",
    "NOT_READY",
]

ALREADY_REVERTED = "already_reverted"
NOT_READY = "not_ready"


def repo_for_edit(store: Store, settings: Settings, edit: SkillEdit) -> SkillsRepo:
    options = load_options(store, edit.edit_id)
    repo = SkillsRepo(Path(options.repo_dir)) if options.repo_dir else operator_repo(settings)
    if not (repo.main_dir / ".git").exists():
        raise ConflictError("this run's skills repo was reset", code="session_reset")
    return repo


def _ready_edit(store: Store, verdict: Verdict) -> SkillEdit:
    """The verdict's edit, once its pipeline is terminal (the automatic action has run)."""
    if store.edit_state(verdict.edit_id).state not in TERMINAL:
        raise ConflictError("this edit is still being decided", code=NOT_READY)
    return store.load_edit(verdict.edit_id)


def _rollback(store: Store, settings: Settings, verdict_id: str, actor: str) -> Verdict:
    verdict = store.load_verdict(verdict_id)
    edit = _ready_edit(store, verdict)
    if verdict.action == "reverted":
        raise ConflictError("this edit is already rolled back", code=ALREADY_REVERTED)
    commit: str | None = None
    merged = verdict.action == "merged"
    if not verdict.recorded:
        repo = repo_for_edit(store, settings, edit)
        result = repo.revert_commit(edit.commit, require_main=merged)
        if merged and not result.main_advanced:  # defence in depth; pre-checked under lock
            raise ConflictError(copy.ERROR_COPY[STACKED_ON_HELD], code=STACKED_ON_HELD)
        commit = result.commit
    updated = verdict.model_copy(
        update={
            "action": "reverted",
            "action_commit": commit,
            "rolled_back_by": actor,
            "rolled_back_at": clock.now(),
        }
    )
    store.save_verdict(updated)
    payload: dict[str, object] = {
        "verdict_id": verdict_id,
        "edit_id": edit.edit_id,
        "actor": actor,
        "commit": commit,
        "affected_skills": list(edit.affected_skills),
        "toast": copy.action_toast("rollback", verdict.action, edit.affected_skills),
    }
    store.emit("verdict.rolled_back", verdict_id, payload, session_hash=verdict.session_hash)
    return updated


def _merge_held(store: Store, repo: SkillsRepo, verdict: Verdict, edit: SkillEdit) -> str:
    target = merge_target(store, repo, edit)
    if target is None:
        raise ConflictError(copy.ERROR_COPY[STACKED_ON_HELD], code=STACKED_ON_HELD)
    return repo.merge_candidate(verdict.verdict_id, commit=target)


def _merge_reverted(store: Store, repo: SkillsRepo, verdict: Verdict, edit: SkillEdit) -> str:
    """Approve anyway: re-apply the reverted change on candidate, then merge it."""
    if not verdict.action_commit:
        raise ConflictError("this edit's revert has not run yet", code=NOT_READY)
    # The re-apply commit lands on top of candidate's head, so everything below it rides along.
    if unresolved_ancestors(store, repo, edit, target=repo.candidate_head()):
        raise ConflictError(copy.ERROR_COPY[STACKED_ON_HELD], code=STACKED_ON_HELD)
    reapplied = repo.reapply(verdict.action_commit)
    return repo.merge_candidate(verdict.verdict_id, commit=reapplied)


def _approve(store: Store, settings: Settings, verdict_id: str, actor: str) -> Verdict:
    verdict = store.load_verdict(verdict_id)
    edit = _ready_edit(store, verdict)
    if verdict.action == "merged":
        return verdict
    override = verdict.action == "reverted"
    commit: str | None = None
    if not verdict.recorded:
        repo = repo_for_edit(store, settings, edit)
        merge = _merge_reverted if override else _merge_held
        commit = merge(store, repo, verdict, edit)
    updated = verdict.model_copy(
        update={"action": "merged", "action_commit": commit, "approved_at": clock.now()}
    )
    store.save_verdict(updated)
    payload: dict[str, object] = {
        "verdict_id": verdict_id,
        "edit_id": edit.edit_id,
        "action": "merged",
        "commit": commit,
        "actor": actor,
        "override": override,
        "affected_skills": list(edit.affected_skills),
        "toast": copy.action_toast("approve", verdict.action, edit.affected_skills),
    }
    store.emit("verdict.acted", verdict_id, payload, session_hash=verdict.session_hash)
    return updated


def _locked(store: Store, verdict_id: str, action: Callable[[], Verdict]) -> Verdict:
    store.acquire_action_lock(verdict_id)
    try:
        return action()
    finally:
        store.release_action_lock(verdict_id)


async def rollback(store: Store, settings: Settings, verdict_id: str, *, actor: str) -> Verdict:
    return await asyncio.to_thread(
        _locked, store, verdict_id, lambda: _rollback(store, settings, verdict_id, actor)
    )


async def approve(store: Store, settings: Settings, verdict_id: str, *, actor: str) -> Verdict:
    return await asyncio.to_thread(
        _locked, store, verdict_id, lambda: _approve(store, settings, verdict_id, actor)
    )
