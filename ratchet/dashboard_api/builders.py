"""Build view models from stored JSON (no model is called on page load).

All functions are synchronous and touch the disk; routes call them via ``asyncio.to_thread``.
"""

from __future__ import annotations

import re
from collections import Counter

from ratchet import clock
from ratchet.dashboard_api.ports import Backend
from ratchet.dashboard_api.queries import (
    TERMINAL_STATES,
    edit_for_verdict,
    visible_verdict_ids,
)
from ratchet.dashboard_api.views import (
    EditView,
    PairView,
    Progress,
    TraceSummary,
    VerdictSummary,
    VerdictView,
)
from ratchet.errors import ForbiddenError, NotFoundError
from ratchet.models import PairOutcome, PairVerdict, Trace, Verdict
from ratchet.store import Store
from ratchet.util import preview

__all__ = [
    "check_id",
    "load_verdict_or_none",
    "verdict_summary",
    "verdict_view",
    "pair_view",
    "edit_view",
    "trace_summary",
    "trace_visible",
    "visible_trace_ids",
    "require_own_verdict",
    "require_own_edit",
    "require_own_pair",
]

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def check_id(value: str, what: str) -> str:
    """Ids become path segments in the store; anything odd is simply not found."""
    if not _ID.fullmatch(value):
        raise NotFoundError(f"{what} {value!r} not found")
    return value


def load_verdict_or_none(store: Store, verdict_id: str) -> Verdict | None:
    if not store.verdict_path(verdict_id).exists():
        return None
    return store.load_verdict(verdict_id)


def verdict_summary(verdict: Verdict) -> VerdictSummary:
    counts: Counter[PairOutcome] = Counter(pair.outcome for pair in verdict.pairs)
    return VerdictSummary(
        verdict_id=verdict.verdict_id,
        edit_id=verdict.edit_id,
        decision=verdict.decision,
        action=verdict.action,
        headline=verdict.headline or verdict.narrative.split(". ")[0],
        affected_skills=verdict.affected_skills,
        usd_total=round(sum(verdict.cost.values()), 6),
        created_at=verdict.created_at,
        n_pairs=len(verdict.pairs),
        outcome_counts=dict(counts),
        recorded=verdict.recorded,
        simulated_models=verdict.simulated_models,
    )


def _progress(backend: Backend, store: Store, edit_id: str) -> Progress | None:
    raw = backend.progress_for(store, edit_id)
    return None if raw is None else Progress.model_validate(raw.model_dump(mode="json"))


def verdict_view(store: Store, backend: Backend, verdict_id: str) -> VerdictView:
    check_id(verdict_id, "verdict")
    verdict = load_verdict_or_none(store, verdict_id)
    edit_id = verdict.edit_id if verdict is not None else edit_for_verdict(store, verdict_id)
    if edit_id is None:
        raise NotFoundError(f"verdict {verdict_id} not found")
    edit = store.load_edit(edit_id)
    try:
        state = store.edit_state(edit_id).state
    except NotFoundError:
        state = "done" if verdict is not None else "detected"
    progress = None
    if verdict is None or state not in TERMINAL_STATES:
        progress = _progress(backend, store, edit_id)
    return VerdictView(
        verdict_id=verdict_id,
        state=state,
        verdict=verdict,
        edit=edit,
        diff=store.load_diff(edit_id),
        progress=progress,
    )


def _find_pair(
    store: Store, verdict_id: str, golden: str, replay: str
) -> tuple[PairVerdict, Verdict | None]:
    verdict = load_verdict_or_none(store, verdict_id)
    if verdict is not None:
        for pair in verdict.pairs:
            if pair.golden_run_id == golden and pair.replay_run_id == replay:
                return pair, verdict
        raise NotFoundError(f"pair {golden}/{replay} not found in {verdict_id}")
    edit_id = edit_for_verdict(store, verdict_id)
    if edit_id is None:
        raise NotFoundError(f"verdict {verdict_id} not found")
    # While judging, the orchestrator persists each judged pair
    # (§3: edits/<id>/pairs/<golden>.json).
    path = store.edit_dir(edit_id) / "pairs" / f"{golden}.json"
    if path.exists():
        pair = store.load_model(path, PairVerdict)
        if pair.replay_run_id == replay:
            return pair, None
    raise NotFoundError(f"pair {golden}/{replay} not found in {verdict_id}")


def pair_view(store: Store, verdict_id: str, golden: str, replay: str) -> PairView:
    for value, what in ((verdict_id, "verdict"), (golden, "trace"), (replay, "trace")):
        check_id(value, what)
    pair, verdict = _find_pair(store, verdict_id, golden, replay)
    return PairView(
        verdict_id=verdict_id,
        decision=verdict.decision if verdict is not None else None,
        pair=pair,
        golden=store.load_trace(golden),
        replay=store.load_trace(replay),
        recorded=bool(verdict is not None and verdict.recorded),
    )


def edit_view(store: Store, backend: Backend, edit_id: str) -> EditView:
    check_id(edit_id, "edit")
    edit = store.load_edit(edit_id)
    try:
        status = store.edit_state(edit_id)
        state, verdict_id = status.state, status.verdict_id
    except NotFoundError:
        state, verdict_id = "detected", None
    return EditView(
        edit=edit,
        diff=store.load_diff(edit_id),
        verdict_id=verdict_id or backend.verdict_id_for(edit_id),
        state=state,
    )


def trace_summary(trace: Trace, last_replayed_at: str | None) -> TraceSummary:
    return TraceSummary(
        run_id=trace.run_id,
        kind=trace.kind,
        task_input=preview(trace.task_input, 400),
        task_label=trace.task_label,
        skills_invoked=trace.skills_invoked,
        world_dependent=trace.world_dependent,
        n_facts=len(trace.world_facts),
        last_replayed_at=clock.parse(last_replayed_at) if last_replayed_at else None,
        blessed_at=trace.blessed_at,
        created_at=trace.created_at,
    )


def _pairs_of(store: Store, verdict_ids: list[str]) -> list[PairVerdict]:
    pairs: list[PairVerdict] = []
    for verdict_id in verdict_ids:
        verdict = load_verdict_or_none(store, verdict_id)
        if verdict is not None:
            pairs.extend(verdict.pairs)
    return pairs


def visible_trace_ids(store: Store, session_hash: str | None) -> frozenset[str]:
    """Public host: every trace id cited by a verdict this caller can list."""
    pairs = _pairs_of(store, visible_verdict_ids(store, session_hash))
    return frozenset(i for p in pairs for i in (p.golden_run_id, p.replay_run_id))


def trace_visible(store: Store, run_id: str, session_hash: str | None) -> bool:
    """Public host: a trace is readable only if a verdict this caller can list cites it."""
    return run_id in visible_trace_ids(store, session_hash)


_SHARED = "Actions on the shared demo history are off. Run a scenario to act on its verdict."
_OTHER = "This verdict belongs to another demo session."


def require_own_verdict(store: Store, verdict_id: str, session_hash: str | None) -> None:
    """Public host: only the session that ran a scenario may act on its verdict."""
    check_id(verdict_id, "verdict")
    row = store.query_one("SELECT session_hash FROM verdicts WHERE verdict_id = ?", (verdict_id,))
    if row is None:
        raise NotFoundError(f"verdict {verdict_id} not found")
    owner = row["session_hash"]
    if owner is None:
        raise ForbiddenError(_SHARED, code="not_public")
    if owner != session_hash:
        raise ForbiddenError(_OTHER, code="not_public")


def require_own_edit(store: Store, edit_id: str, session_hash: str | None) -> None:
    check_id(edit_id, "edit")
    row = store.query_one("SELECT session_hash FROM edits WHERE edit_id = ?", (edit_id,))
    if row is None:
        raise NotFoundError(f"edit {edit_id} not found")
    if row["session_hash"] is None:
        raise ForbiddenError(_SHARED, code="not_public")
    if row["session_hash"] != session_hash:
        raise ForbiddenError("This edit belongs to another demo session.", code="not_public")


def require_own_pair(store: Store, golden: str, replay: str, session_hash: str | None) -> None:
    check_id(golden, "trace")
    check_id(replay, "trace")
    ids = visible_verdict_ids(store, session_hash, own_only=True)
    pairs = _pairs_of(store, ids)
    if not any(p.golden_run_id == golden and p.replay_run_id == replay for p in pairs):
        raise ForbiddenError(
            "Re-bless works on pairs from your own scenario runs.", code="not_public"
        )
