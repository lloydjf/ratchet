"""Pydantic output schemas for every structured prompt.

Each model is exported to ``ratchet/judge/prompts/<name>.schema.json``
(:func:`ratchet.judge.prompts.export_schemas`); a unit test fails when the committed files
drift from these models. The prompts' list caps (``reasons`` ≤ 3,
``evidence_steps`` ≤ 4) are advertised in the schema but trimmed leniently on input, so a
model that cites five steps is not burned on a retry for it.
"""

from __future__ import annotations

from typing import Annotated, Any  # Any: raw validator inputs, see below

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ratchet.models import OpinionVerdict, SideEffectClass

__all__ = [
    "WorldFactOut",
    "WorldFactsOut",
    "SideEffectOut",
    "CriteriaOut",
    "ScreenOut",
    "CriterionOut",
    "CheckOut",
    "AdjudicateOut",
    "AttributeDriftOut",
    "CompareFactOut",
    "InjectRegressionOut",
]


def _trim(limit: int) -> BeforeValidator:
    # Any: a pydantic before-validator sees the raw, not-yet-validated model output.
    def trim(value: Any) -> Any:
        return value[:limit] if isinstance(value, list) else value

    return BeforeValidator(trim)


# Any: a pydantic before-validator sees the raw, not-yet-validated model output.
def _clamp01(value: Any) -> Any:
    """Models sometimes answer 85 for 0.85; accept percentages, clamp to [0, 1]."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        number = float(value)
        if 1.0 < number <= 100.0:
            number /= 100.0
        return min(max(number, 0.0), 1.0)
    return value


Confidence = Annotated[float, Field(ge=0.0, le=1.0), BeforeValidator(_clamp01)]
Reasons = Annotated[list[str], _trim(3), Field(max_length=3)]
Evidence = Annotated[list[str], _trim(4), Field(max_length=4)]


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


# Any: pydantic's json_schema_extra hook hands over an arbitrary JSON-schema dict.
def _require_value(schema: dict[str, Any]) -> None:
    required = schema.setdefault("required", [])
    if "value" not in required:
        required.append("value")


class WorldFactOut(_Out):
    # The schema sent to the model lists `value` as required, but validation tolerates its
    # absence: live Nano omitted it even in strict mode, and one missing field must not fail
    # the whole answer. bless.fact_value() recovers it from the statement.
    model_config = ConfigDict(extra="ignore", json_schema_extra=_require_value)

    fact_id: str
    statement: str
    source_step: str
    volatile: bool
    query_hint: str
    value: str = ""  # + bare observed value (models.WorldFact.value)


class WorldFactsOut(_Out):
    facts: list[WorldFactOut]


class SideEffectOut(_Out):
    side_effect: SideEffectClass
    reason: str


class CriteriaOut(_Out):
    criteria: Annotated[list[str], _trim(6), Field(max_length=6)]


class ScreenOut(_Out):
    """The JudgeOpinion fields a model fills (tier and usd are ours)."""

    verdict: OpinionVerdict
    confidence: Confidence
    reasons: Reasons
    evidence_steps: Evidence = Field(default_factory=list)


class CriterionOut(_Out):
    criterion: str
    met: bool
    evidence_steps: list[str] = Field(default_factory=list)


class CheckOut(ScreenOut):
    """JudgeOpinion plus per-criterion findings."""

    reasons: Annotated[list[str], _trim(5), Field(max_length=5)]
    evidence_steps: Annotated[list[str], _trim(8), Field(max_length=8)] = Field(
        default_factory=list
    )
    criteria_met: list[CriterionOut] = Field(default_factory=list)
    failure_summary: str = Field(
        "",
        description="Only when verdict is regressed: what got worse, as a verb phrase of at "
        "most 8 words that completes 'the skill now ...', e.g. 'drops the VAT line'. Else empty.",
    )


class AdjudicateOut(CheckOut):
    """Check output plus a causal explanation (≤ 80 words) and a possible drift fact."""

    causal_explanation: str = ""
    possible_drift_fact: str | None = None
    caused_by_diff: bool = Field(
        True,
        description="True only if the skill diff plausibly causes the difference. False when "
        "the diff is empty or cosmetic, or the difference is best explained by "
        "nondeterminism or a changed external fact.",
    )


class AttributeDriftOut(_Out):
    attributable: bool
    confidence: Confidence
    reason: str = ""


class CompareFactOut(_Out):
    """Nano's old-vs-new fact comparison (drift check step 2)."""

    changed: bool
    new_value: str | None = None
    confidence: Confidence


class InjectRegressionOut(_Out):
    """Regression injection as the smallest edit rather than the whole file. Asked for the full
    SKILL.md, Ultra (thinking off) described a change in ``change_summary`` but returned the
    file byte-identical, or broke the YAML front matter; a find/replace pair is applied by
    Ratchet, so the rest of the file cannot drift."""

    find: str = Field(description="An excerpt copied verbatim from the original; exactly once.")
    replace: str = Field(description="What replaces the excerpt.")
    change_summary: str
