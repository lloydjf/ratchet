"""LLM-injected regressions (dataset construction, step 2).

For each skill and each of R1–R6, Ultra writes 2 edited SKILL.md variants with
``inject_regression.md`` (purpose ``bench``, so the proxy's $12 bench cap applies).
A variant is kept only if it is a valid agentskills.io skill with the same name, differs from
the original, and **manifests**: replaying it (``execute.replay_only``: no judge) makes at
least one selected golden diverge. Otherwise it is regenerated, up to 3 tries; a variant that
never manifests is dropped and logged in ``labels.json`` → ``dropped``.

Each try changes the request (variant and attempt numbers are appended to the type
definition), so the proxy's response cache never hands back the same rejected edit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ratchet.bench.env import BenchEnv
from ratchet.bench.execute import EditSpec, replay_only
from ratchet.bench.labels import EditLabel, write_edit
from ratchet.bench.taxonomy import DEFINITIONS, EditType, expected_decision
from ratchet.judge.prompts import get_spec
from ratchet.judge.schemas import InjectRegressionOut
from ratchet.judge.structured import ChatClient, call_structured
from ratchet.log import get_logger
from ratchet.orchestrator.service import Orchestrator
from ratchet.skills import parse_skill_md, validate_skill

__all__ = [
    "MAX_TRIES",
    "VARIANTS",
    "Dropped",
    "variant_id",
    "definition_for",
    "check_variant",
    "apply_edit",
    "inject_one",
]

MAX_TRIES = 3
VARIANTS = 2
_log = get_logger("bench")


@dataclass(frozen=True)
class Dropped:
    edit_id: str
    skill: str
    type: str
    tries: int
    problems: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "edit_id": self.edit_id,
            "skill": self.skill,
            "type": self.type,
            "tries": self.tries,
            "problems": self.problems,
        }


def variant_id(edit_type: str, skill: str, variant: int) -> str:
    return f"{edit_type.lower()}-{skill}-{variant}"


def definition_for(
    edit_type: EditType, variant: int, attempt: int, previous_issue: str | None = None
) -> str:
    text = DEFINITIONS[edit_type]
    if variant > 1:
        text += f". This is variant {variant}: change a different step than the most obvious one"
    if attempt > 1:
        # Live construction: without this, Ultra repeated the same rejected edit three times.
        why = previous_issue or "the previous edit did not change the skill's behavior"
        text += f". Attempt {attempt}: the previous attempt was rejected because {why}"
    return text


def counts_as_manifestation(edit_type: EditType, selected_as: str) -> bool:
    """Which diverging replays prove a regression manifested. A direct golden (it invoked
    the skill) always does. A trigger-neighborhood golden only does for R5, where the edit
    is supposed to make the skill fire on tasks it never handled. Canaries and other
    neighbors diverge only by nondeterminism, so they are never labeled as regressions
    (otherwise ``csv-*`` goldens would be labeled as expected regressions of expense-report)."""
    if selected_as == "direct":
        return True
    return selected_as == "neighborhood" and edit_type == "R5"


def _feedback(issue: str | None) -> str | None:
    """What to tell the model about the last rejected attempt, in its own terms."""
    if not issue:
        return None
    if issue.startswith("excerpt"):
        return (
            "the find excerpt was not copied verbatim from the original or did not occur "
            "exactly once"
        )
    if issue.startswith("unparseable") or "description" in issue or "name" in issue:
        return "the YAML front matter was altered or broken (keep it exactly as given)"
    if issue == "identical to the original":
        return "the file came back unchanged"
    if issue == "did not manifest on any golden":
        return "the edit did not change the skill's behavior on any past task"
    return issue


def check_variant(original: str, edited: str, skill: str) -> list[str]:
    """Why an injected SKILL.md is unusable (empty = usable)."""
    if edited.strip() == original.strip():
        return ["identical to the original"]
    try:
        doc = parse_skill_md(edited)
    except Exception as exc:  # noqa: BLE001 - any parse failure disqualifies the variant
        return [f"unparseable: {type(exc).__name__}"]
    problems = validate_skill(doc, dir_name=skill)
    if doc.name != skill:
        problems.append("skill name changed")
    return problems


async def _generate(
    client: ChatClient,
    original: str,
    edit_type: EditType,
    variant: int,
    attempt: int,
    previous_issue: str | None = None,
) -> tuple[str | None, str, str | None]:
    spec = get_spec("inject_regression")
    result = await call_structured(
        client,
        prompt=spec.name,
        tier=spec.tier,
        inputs={
            "type_code": edit_type,
            "type_definition": definition_for(edit_type, variant, attempt, previous_issue),
            "skill_md": original,
        },
        schema=InjectRegressionOut,
        max_tokens=spec.max_tokens,
    )
    if result.value is None:
        return None, "", result.failure_code or "schema_failure"
    edited, issue = apply_edit(original, result.value.find, result.value.replace)
    return edited, result.value.change_summary.strip(), issue


def apply_edit(original: str, find: str, replace: str) -> tuple[str | None, str | None]:
    """Apply a find/replace edit; the excerpt must occur exactly once (line endings and
    trailing whitespace are normalized on both sides so a copied excerpt still matches)."""
    needle = find.strip("\n")
    if len(needle.strip()) < 12:
        return None, "excerpt too short"
    count = original.count(needle)
    if count != 1:
        return None, "excerpt not found verbatim" if count == 0 else "excerpt not unique"
    edited = original.replace(needle, replace.strip("\n"), 1)
    return edited.rstrip("\n") + "\n", None


async def inject_one(
    env: BenchEnv,
    orchestrator: Orchestrator,
    client: ChatClient,
    *,
    skill: str,
    edit_type: EditType,
    variant: int,
    on_try: Callable[[str, int, str], None] | None = None,
) -> EditLabel | Dropped:
    """Generate, validate and manifestation-check one variant (up to :data:`MAX_TRIES`)."""
    edit_id = variant_id(edit_type, skill, variant)
    path = f"{skill}/SKILL.md"
    original = (env.paths.skills / path).read_text(encoding="utf-8")
    task_for_run = env.task_for_run()
    problems: list[str] = []
    issue: str | None = None
    for attempt in range(1, MAX_TRIES + 1):
        edited, summary, failure = await _generate(
            client, original, edit_type, variant, attempt, _feedback(issue)
        )
        issue = failure or "; ".join(check_variant(original, edited or "", skill))
        if not issue:
            assert edited is not None
            spec = EditSpec(edit_id, skill, {path: edited})
            divergences = await replay_only(env, orchestrator, spec)
            affected = sorted(
                task_for_run.get(d.golden_run_id, d.golden_run_id)
                for d in divergences
                if d.diverged and counts_as_manifestation(edit_type, d.selected_as)
            )
            if affected:
                write_edit(env.edits_root, edit_id, {path: edited})
                _log.info("bench.inject.kept", edit_id=edit_id, attempt=attempt)
                return EditLabel(
                    edit_id=edit_id,
                    skill=skill,
                    type=edit_type,
                    affected_goldens=affected,
                    expected_decision=expected_decision(edit_type),
                    expected_pair_outcomes=dict.fromkeys(affected, "regression"),
                    change_summary=summary,
                    source="sim" if env.settings.simulated else "llm",
                    manifested=True,
                    attempts=attempt,
                )
            issue = "did not manifest on any golden"
        problems.append(f"try {attempt}: {issue}")
        if on_try is not None:
            on_try(edit_id, attempt, issue)
    _log.warning("bench.inject.dropped", edit_id=edit_id, tries=MAX_TRIES)
    return Dropped(edit_id, skill, edit_type, MAX_TRIES, problems)
