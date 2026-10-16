"""The live progress strip for a running edit, derived from its events.

Stages (``ratchet.copy.STAGE_KEYS``): detected → selecting → replaying → level0 → nano →
super → ultra → controls → drift → summary → done. The current stage comes from
``edits.state`` and, while judging, from the deepest tier that has given an opinion so far.
Replay progress ``k/n`` sums the latest ``batch.*`` event per batch, so the same view works
for a recorded (replayed-mode) run; control-replay batches (``ctrl_…``, ADR 0023) count
toward ``controls`` instead, and the retry round for crashed replays (``retry_…``) only
sets the replaying stage's detail ("retrying 1 failed replay"). A stage that was passed
without work (a tier the cascade never needed, nothing contested, a drift check with
nothing eligible) is ``skipped``. ``stalled`` is true when nothing moved for
``STALL_AFTER_S`` seconds (a visible change is wanted every 10 s).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from ratchet import clock, copy
from ratchet.models import TIERS, Event, Tier
from ratchet.orchestrator.ids import CONTROL_BATCH_PREFIX, RETRY_BATCH_PREFIX
from ratchet.store import Store

__all__ = [
    "StageView",
    "Progress",
    "progress_for",
    "STALL_AFTER_S",
    "stage_for_state",
    "STATE_STAGE",
]

STALL_AFTER_S = 20.0
StageStatus = Literal["pending", "active", "done", "skipped"]
# pipeline state -> stage key (the verdict timeline uses the same mapping).
STATE_STAGE: dict[str, str] = {
    "detected": "detected",
    "selecting": "selecting",
    "dispatching": "replaying",
    "collecting": "replaying",
    "judging": "level0",
    "controlling": "controls",
    "drifting": "drift",
    "deciding": "summary",
    "acting": "done",
    "notifying": "done",
}


class StageView(BaseModel):
    key: str
    label: str
    status: StageStatus
    detail: str = ""


class Progress(BaseModel):
    stage: str
    stage_label: str
    k: int
    n: int
    elapsed_s: float
    usd_by_tier: dict[Tier, float]
    stalled: bool
    stages: list[StageView]


def stage_for_state(state: str, tiers_seen: list[Tier]) -> str:
    stage = STATE_STAGE.get(state, "done")
    if stage == "level0" and tiers_seen:
        return tiers_seen[-1]
    return stage


class _Facts:
    def __init__(self, events: list[Event]) -> None:
        self.batches: dict[str, tuple[int, int]] = {}
        self.controls: dict[str, tuple[int, int]] = {}
        self.retries: dict[str, tuple[int, int]] = {}
        self.retries_done: set[str] = set()
        self.n_control = 0
        self.opinions: Counter[str] = Counter()
        self.n_selected: int | None = None
        self.n_level0 = 0
        self.n_drift = 0
        self.states: set[str] = set()
        self.last: datetime | None = None
        for event in events:
            self._add(event)

    def _add(self, event: Event) -> None:
        p = event.payload
        self.last = event.ts if self.last is None or event.ts > self.last else self.last
        if event.kind.startswith("batch."):
            k, n = p.get("k", 0), p.get("n", 0)
            batch_id = str(p.get("batch_id"))
            into = self.controls if batch_id.startswith(CONTROL_BATCH_PREFIX) else self.batches
            if batch_id.startswith(RETRY_BATCH_PREFIX):
                into = self.retries
                if event.kind == "batch.finished":
                    self.retries_done.add(batch_id)
            into[batch_id] = (
                int(k) if isinstance(k, int) else 0,
                int(n) if isinstance(n, int) else 0,
            )
        elif event.kind == "pair.control":
            self.n_control += 1
        elif event.kind == "pair.opinion":
            self.opinions[str(p.get("tier"))] += 1
        elif event.kind == "pair.level0":
            self.n_level0 += 1
        elif event.kind == "pair.drift":
            self.n_drift += 1
        elif event.kind == "edit.selected":
            n_traces = p.get("n_traces")
            self.n_selected = int(n_traces) if isinstance(n_traces, int) else None
        elif event.kind == "orchestrator.state":
            self.states.add(str(p.get("from")))

    @property
    def kn(self) -> tuple[int, int]:
        k = sum(k for k, _ in self.batches.values())
        n = sum(n for _, n in self.batches.values()) or (self.n_selected or 0)
        return k, n

    @property
    def control_kn(self) -> tuple[int, int]:
        k = sum(k for k, _ in self.controls.values())
        n = sum(n for _, n in self.controls.values())
        return k, n

    def tiers(self) -> list[Tier]:
        return [t for t in TIERS if self.opinions[t]]


def _label(key: str, facts: _Facts, runner: str) -> str:
    k, n = facts.kn
    if key == "selecting":
        return copy.stage_label(key, n=facts.n_selected)
    if key == "replaying":
        return (
            copy.stage_label(key, k, n, runner=runner)
            if n
            else copy.stage_label(key, runner=runner)
        )
    if key in TIERS:
        count = facts.opinions[key]
        return copy.stage_label(key, n=count) if count else copy.stage_label(key)
    if key == "controls":
        ck, cn = facts.control_kn
        return copy.stage_label(key, ck, cn) if cn else copy.stage_label(key)
    if key == "drift":
        return copy.stage_label(key, n=facts.n_drift) if facts.n_drift else copy.stage_label(key)
    return copy.stage_label(key)


def _status(key: str, current: str, facts: _Facts) -> StageStatus:
    order = copy.STAGE_KEYS
    here, mine = order.index(current), order.index(key)
    if mine == here:
        return "active"
    if mine > here:
        return "pending"
    if key in TIERS and not facts.opinions[key]:
        return "skipped"
    if key == "drift" and not facts.n_drift:
        return "skipped"
    if key == "controls" and not (facts.controls or facts.n_control):
        return "skipped"
    if key == "replaying" and not facts.batches and "dispatching" not in facts.states:
        return "skipped"
    return "done"


def _runner_label(facts_events: list[Event]) -> str:
    for event in reversed(facts_events):
        runner = event.payload.get("runner")
        if event.kind.startswith("batch.") and isinstance(runner, str):
            return "Nebius Jobs" if runner.startswith("nebius") else "the local runner"
    return "the local runner"


def progress_for(store: Store, edit_id: str) -> Progress | None:
    """The progress strip for ``edit_id``; ``None`` once the edit is done."""
    state = store.edit_state(edit_id)
    if state.state in ("done", "failed_infra"):
        return None
    edit = store.load_edit(edit_id)
    events = store.events(ref=edit_id, limit=1000)
    facts = _Facts(events)
    current = stage_for_state(state.state, facts.tiers())
    runner = _runner_label(events)
    stages = [
        StageView(
            key=key,
            label=_label(key, facts, runner),
            status=_status(key, current, facts),
            detail=_detail(key, facts),
        )
        for key in copy.STAGE_KEYS
    ]
    now = clock.now()
    last = max(t for t in (facts.last, state.state_updated_at) if t is not None)
    k, n = facts.kn
    return Progress(
        stage=current,
        stage_label=_label(current, facts, runner),
        k=k,
        n=n,
        elapsed_s=round(max((now - edit.detected_at).total_seconds(), 0.0), 1),
        usd_by_tier=dict(store.spend(edit_id=edit_id).usd),
        stalled=(now - last).total_seconds() > STALL_AFTER_S,
        stages=stages,
    )


def _detail(key: str, facts: _Facts) -> str:
    if key == "level0" and facts.n_level0:
        return f"{facts.n_level0} pair(s) checked"
    if key in TIERS and facts.opinions[key]:
        return f"{facts.opinions[key]} opinion(s)"
    if key == "controls" and facts.n_control:
        return f"{facts.n_control} pair(s) re-checked"
    if key == "replaying" and facts.retries:
        n = sum(n for _, n in facts.retries.values())
        return copy.retry_detail(n, done=facts.retries_done >= set(facts.retries))
    return ""
