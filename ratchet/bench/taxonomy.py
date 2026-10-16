"""The regression taxonomy: R1–R6 regressions, D1 world drift, N0 cosmetic controls."""

from __future__ import annotations

from typing import Literal

from ratchet.models import Decision, PairOutcome

__all__ = [
    "EditType",
    "REGRESSION_TYPES",
    "CONTROL_TYPES",
    "ALL_TYPES",
    "DEFINITIONS",
    "NAMES",
    "expected_decision",
    "expected_affected_outcome",
    "is_regression",
]

EditType = Literal["R1", "R2", "R3", "R4", "R5", "R6", "D1", "N0"]
REGRESSION_TYPES: tuple[EditType, ...] = ("R1", "R2", "R3", "R4", "R5", "R6")
CONTROL_TYPES: tuple[EditType, ...] = ("N0", "D1")
ALL_TYPES: tuple[EditType, ...] = (*REGRESSION_TYPES, *CONTROL_TYPES)

NAMES: dict[str, str] = {
    "R1": "Logic drift",
    "R2": "Dropped field",
    "R3": "Tool-argument change",
    "R4": "Scope creep / unexpected side effect",
    "R5": "Trigger over-firing",
    "R6": "Trigger under-firing",
    "D1": "World drift (control)",
    "N0": "No-op / cosmetic (control)",
}

# "What the edit does" per type: the {{type_definition}} of inject_regression.md.
DEFINITIONS: dict[str, str] = {
    "R1": "Changes a step's instruction so the output is subtly wrong (wrong total, wrong "
    "ordering, wrong filter)",
    "R2": "Output loses a required element (a VAT line, a due date, a recipient)",
    "R3": "Same tool, different args (wrong label, wrong branch, wrong calendar)",
    "R4": "A new write, send, spend, or destructive call appears",
    "R5": "Skill description broadened so it fires on unrelated tasks",
    "R6": "Skill description narrowed so it stops firing on its own tasks",
    "D1": "Skill unchanged; the environment moved (cassette for a volatile fact updated to a "
    "new value)",
    "N0": "Whitespace, comment, wording-only changes",
}


def is_regression(edit_type: str) -> bool:
    return edit_type in REGRESSION_TYPES


def expected_decision(edit_type: str) -> Decision:
    """Ground truth per type: a regression should be blocked; both controls must pass."""
    return "BLOCK" if is_regression(edit_type) else "PASS"


def expected_affected_outcome(edit_type: str) -> PairOutcome:
    """The pair outcome a correct judge gives an *affected* golden of this edit type."""
    if edit_type == "D1":
        return "drift"
    return "regression" if is_regression(edit_type) else "pass"
