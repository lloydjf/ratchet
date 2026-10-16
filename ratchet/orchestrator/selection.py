"""Affected-trace selection.

1. **direct** — non-archived goldens whose ``skills_invoked`` meet ``edit.affected_skills``
   (newest first when the cap bites).
2. **neighborhood** — other goldens whose ``task_input`` is close to an affected skill's
   ``description``: the max cosine over the description at the parent (main) and at the
   candidate commit, so both a narrowed and a widened trigger are caught.
3. **canary** — 3 fixed goldens that invoke no skills: the ones flagged by the caller,
   else the 3 oldest. A canary that also qualifies as a neighbor is labelled neighborhood.

The list is capped (default 40) in that priority order (neighborhood by similarity,
descending) and batched into groups of 10 for jobs.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence

from ratchet.errors import RatchetError
from ratchet.log import get_logger
from ratchet.models import SkillEdit
from ratchet.orchestrator.embed import Embedder, LocalHashEmbedder, cosine
from ratchet.store import SelectedTrace, Store, TraceRow
from ratchet.watcher.gitops import SkillsRepo

__all__ = ["select_traces", "make_batches", "selection_counts", "N_CANARIES"]

N_CANARIES = 3
_TASK_CHARS = 4000  # embedding input cap per task_input
_log = get_logger("selection")


def _pick_canaries(rows: list[TraceRow], flagged: Sequence[str] | None) -> list[TraceRow]:
    skill_free = [r for r in rows if not r.skills]
    by_id = {r.run_id: r for r in skill_free}
    chosen = [by_id[run_id] for run_id in dict.fromkeys(flagged or ()) if run_id in by_id]
    oldest = sorted(skill_free, key=lambda r: (r.created_at, r.run_id))
    for row in oldest:
        if len(chosen) >= N_CANARIES:
            break
        if row not in chosen:
            chosen.append(row)
    return chosen[:N_CANARIES]


def _descriptions(repo: SkillsRepo, edit: SkillEdit) -> list[str]:
    texts: list[str] = []
    for skill in sorted(set(edit.affected_skills)):
        for ref in (edit.parent, edit.commit):
            text = repo.skill_description(skill, ref=ref)
            if text and text not in texts:
                texts.append(text)
    return texts


def _task_inputs(store: Store, run_ids: list[str]) -> dict[str, str]:
    return {run_id: store.load_trace(run_id).task_input[:_TASK_CHARS] for run_id in run_ids}


async def _similarities(
    descriptions: list[str], tasks: dict[str, str], embedder: Embedder
) -> dict[str, float]:
    order = sorted(tasks)
    vectors = await embedder.embed(descriptions + [tasks[run_id] for run_id in order])
    desc_vecs, task_vecs = vectors[: len(descriptions)], vectors[len(descriptions) :]
    return {
        run_id: max(cosine(task_vec, desc) for desc in desc_vecs)
        for run_id, task_vec in zip(order, task_vecs, strict=True)
    }


async def _neighborhood(
    store: Store,
    repo: SkillsRepo,
    edit: SkillEdit,
    rows: list[TraceRow],
    embedder: Embedder,
    threshold: float | None,
) -> list[tuple[str, float]]:
    if not rows:
        return []
    descriptions = await asyncio.to_thread(_descriptions, repo, edit)
    if not descriptions:
        return []
    tasks = await asyncio.to_thread(_task_inputs, store, [r.run_id for r in rows])
    limit = threshold if threshold is not None else embedder.default_threshold
    try:
        scores = await _similarities(descriptions, tasks, embedder)
    except RatchetError as exc:
        fallback = LocalHashEmbedder()
        _log.warning("selection.embed_failed", edit_id=edit.edit_id, code=exc.code)
        scores = await _similarities(descriptions, tasks, fallback)
        limit = fallback.default_threshold
    hits = [(run_id, score) for run_id, score in scores.items() if score >= limit]
    return sorted(hits, key=lambda hit: (-hit[1], hit[0]))


async def select_traces(
    store: Store,
    repo: SkillsRepo,
    edit: SkillEdit,
    embedder: Embedder,
    *,
    cap: int = 40,
    threshold: float | None = None,
    canaries: Sequence[str] | None = None,
) -> list[SelectedTrace]:
    """Goldens to replay for ``edit``; ``threshold`` defaults to the embedder's."""
    rows = await asyncio.to_thread(
        store.list_traces, kind="golden", include_archived=False, limit=1_000_000
    )
    affected = set(edit.affected_skills)
    direct = sorted(
        (r for r in rows if affected.intersection(r.skills)),
        key=lambda r: (-r.created_at.timestamp(), r.run_id),
    )
    direct_ids = {r.run_id for r in direct}
    others = [r for r in rows if r.run_id not in direct_ids]
    neighbors = await _neighborhood(store, repo, edit, others, embedder, threshold)
    neighbor_ids = {run_id for run_id, _ in neighbors}
    canary_rows = [r for r in _pick_canaries(others, canaries) if r.run_id not in neighbor_ids]
    selected = (
        [SelectedTrace(r.run_id, "direct", None) for r in direct]
        + [SelectedTrace(run_id, "neighborhood", round(s, 4)) for run_id, s in neighbors]
        + [SelectedTrace(r.run_id, "canary", None) for r in canary_rows]
    )[: max(cap, 0)]
    counts = selection_counts(selected)
    _log.info("selection.done", edit_id=edit.edit_id, n_goldens=len(rows), **counts)
    return selected


def selection_counts(selected: Sequence[SelectedTrace]) -> dict[str, int]:
    """The ``edit.selected`` event payload counts."""
    reasons = Counter(s.reason for s in selected)
    return {
        "n_traces": len(selected),
        "n_direct": reasons["direct"],
        "n_neighborhood": reasons["neighborhood"],
        "n_canary": reasons["canary"],
    }


def make_batches(selected: Sequence[SelectedTrace | str], size: int = 10) -> list[list[str]]:
    """Run ids in groups of ``size`` (one replay job per group), order preserved."""
    if size < 1:
        raise ValueError("batch size must be positive")
    ids = [s if isinstance(s, str) else s.run_id for s in selected]
    return [ids[i : i + size] for i in range(0, len(ids), size)]
