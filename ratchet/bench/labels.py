"""Labeled edits (dataset construction, steps 2–4).

An edit lives in ``<edits>/<edit_id>/``: ``files/<repo path>`` holds the full new contents of
every changed file (relative to the skills repo), ``world/`` the world-fixture overrides of a
D1 case and ``tavily/`` its recorded Tavily responses. ``labels.json`` is the index:

``{edit_id, skill, type, affected_goldens, expected_decision, expected_pair_outcomes,
change_summary, source, manifested, attempts, review}``

``affected_goldens`` are golden *task ids* (stable across re-recordings); a golden that is not
in ``expected_pair_outcomes`` is expected to ``pass``. ``review`` records the human pass: 20%
of regression edits and 100% of controls are ``required``; a reviewer agrees, or disagrees and
gives the corrected decision, and may label individual pairs (the κ subset).
"""

from __future__ import annotations

import difflib
import json
import random
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ratchet import clock
from ratchet.bench.taxonomy import EditType, is_regression
from ratchet.errors import ConfigError, NotFoundError
from ratchet.models import Decision, PairOutcome
from ratchet.store.base import write_json_atomic

__all__ = [
    "HumanPairLabel",
    "Review",
    "EditLabel",
    "LabelSet",
    "load_labels",
    "save_labels",
    "upsert",
    "edit_dir",
    "write_edit",
    "edit_files",
    "edit_world",
    "edit_tavily",
    "edit_diff",
    "mark_review_sample",
    "record_review",
    "REVIEW_RATIO",
]

REVIEW_RATIO = 0.20
HumanPairLabel = Literal["regression", "pass", "drift"]
Source = Literal["llm", "deterministic", "hand", "sim"]


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = False
    status: Literal["pending", "agreed", "disagreed", "not_required"] = "not_required"
    reviewer: str | None = None
    at: datetime | None = None
    decision: Decision | None = None  # the reviewer's corrected decision on a disagreement
    note: str = ""
    pair_labels: dict[str, HumanPairLabel] = Field(default_factory=dict)  # task_id -> label


class EditLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_id: str
    skill: str
    type: EditType
    affected_goldens: list[str] = Field(default_factory=list)
    expected_decision: Decision
    expected_pair_outcomes: dict[str, PairOutcome] = Field(default_factory=dict)
    change_summary: str = ""
    source: Source = "hand"
    manifested: bool = True
    attempts: int = 1
    review: Review = Field(default_factory=Review)

    def effective_decision(self) -> Decision:
        """The decision the metrics score against: a reviewer's correction wins."""
        if self.review.status == "disagreed" and self.review.decision is not None:
            return self.review.decision
        return self.expected_decision

    def expected_outcome(self, task_id: str) -> PairOutcome:
        return self.expected_pair_outcomes.get(task_id, "pass")


class LabelSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    labels: list[EditLabel] = Field(default_factory=list)
    dropped: list[dict[str, object]] = Field(default_factory=list)  # manifestation log

    def by_id(self) -> dict[str, EditLabel]:
        return {label.edit_id: label for label in self.labels}

    def get(self, edit_id: str) -> EditLabel:
        found = self.by_id().get(edit_id)
        if found is None:
            raise NotFoundError(f"no labeled edit {edit_id}", code="not_found")
        return found


def load_labels(path: Path) -> LabelSet:
    if not path.is_file():
        return LabelSet()
    return LabelSet.model_validate_json(path.read_text(encoding="utf-8"))


def save_labels(path: Path, labels: LabelSet) -> None:
    ordered = sorted(labels.labels, key=lambda lab: (lab.skill, lab.type, lab.edit_id))
    write_json_atomic(path, labels.model_copy(update={"labels": ordered}))


def upsert(labels: LabelSet, label: EditLabel) -> LabelSet:
    kept = [lab for lab in labels.labels if lab.edit_id != label.edit_id]
    return labels.model_copy(update={"labels": [*kept, label]})


# --- edit files ---------------------------------------------------------------------------------
def edit_dir(edits_root: Path, edit_id: str) -> Path:
    if not edit_id.replace("-", "").replace("_", "").isalnum():
        raise ConfigError(f"bad edit id {edit_id!r}", code="config_error")
    return edits_root / edit_id


def write_edit(
    edits_root: Path,
    edit_id: str,
    files: dict[str, str],
    *,
    world: dict[str, object] | None = None,
    tavily: dict[str, object] | None = None,
) -> Path:
    """(Re)write an edit's directory: ``files/`` and, for D1, ``world/`` and ``tavily/``."""
    root = edit_dir(edits_root, edit_id)
    shutil.rmtree(root, ignore_errors=True)
    for rel, content in files.items():
        target = root / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for sub, payload in (("world", world), ("tavily", tavily)):
        for name, value in (payload or {}).items():
            target = root / sub / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return root


def edit_files(edits_root: Path, edit_id: str) -> dict[str, str | None]:
    """Repo-relative path → new contents, as ``SkillsRepo.apply_files`` takes them."""
    base = edit_dir(edits_root, edit_id) / "files"
    if not base.is_dir():
        raise NotFoundError(f"edit {edit_id} has no files", code="not_found")
    return {
        p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


def edit_world(edits_root: Path, edit_id: str) -> Path | None:
    path = edit_dir(edits_root, edit_id) / "world"
    return path if path.is_dir() else None


def edit_tavily(edits_root: Path, edit_id: str) -> Path | None:
    path = edit_dir(edits_root, edit_id) / "tavily"
    return path if path.is_dir() else None


def edit_diff(edits_root: Path, skills_root: Path, edit_id: str) -> str:
    """Unified diff of the edit against the original bench skills."""
    out: list[str] = []
    for rel, new in edit_files(edits_root, edit_id).items():
        original = skills_root / rel
        old = original.read_text(encoding="utf-8") if original.is_file() else ""
        out += difflib.unified_diff(
            old.splitlines(keepends=True),
            (new or "").splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    return "".join(out)


# --- human review -------------------------------------------------------------------------------
def mark_review_sample(labels: LabelSet, *, seed: int = 0, ratio: float = REVIEW_RATIO) -> LabelSet:
    """Step 4: 100% of controls and a seeded ``ratio`` of regression edits per type."""
    rng = random.Random(seed)
    chosen: set[str] = set()
    by_type: dict[str, list[str]] = {}
    for lab in labels.labels:
        if is_regression(lab.type):
            by_type.setdefault(lab.type, []).append(lab.edit_id)
        else:
            chosen.add(lab.edit_id)
    for ids in by_type.values():
        k = max(1, round(len(ids) * ratio))
        chosen.update(rng.sample(sorted(ids), min(k, len(ids))))
    updated = []
    for lab in labels.labels:
        review = lab.review
        if lab.edit_id in chosen and not review.required:
            review = review.model_copy(update={"required": True, "status": "pending"})
        updated.append(lab.model_copy(update={"review": review}))
    return labels.model_copy(update={"labels": updated})


def record_review(
    labels: LabelSet,
    edit_id: str,
    *,
    agree: bool,
    reviewer: str,
    decision: Decision | None = None,
    note: str = "",
    pair_labels: dict[str, HumanPairLabel] | None = None,
) -> LabelSet:
    label = labels.get(edit_id)
    if not agree and decision is None:
        raise ConfigError("a disagreement needs the corrected decision", code="bad_args")
    review = label.review.model_copy(
        update={
            "status": "agreed" if agree else "disagreed",
            "reviewer": reviewer,
            "at": clock.now(),
            "decision": None if agree else decision,
            "note": note,
            "pair_labels": {**label.review.pair_labels, **(pair_labels or {})},
        }
    )
    return upsert(labels, label.model_copy(update={"review": review}))
