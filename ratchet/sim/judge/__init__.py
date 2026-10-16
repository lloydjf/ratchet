"""Judge-purpose dispatch for the simulator, keyed by ``X-Ratchet-Prompt: <name>@<hash12>``
(design doc §4.2, ADR 0018).

The rendered prompt is the first user message (``call_structured`` sends few-shot examples
as a system message and retries as later turns). Structured prompts answer one JSON object
valid against the prompt's schema; ``narrative`` and ``digest`` answer plain text. An unknown
prompt gets a reply that is not JSON, so the caller's validation fails and the pair becomes
``uncertain`` — never ``pass``. Test infrastructure, never Nemotron output.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ratchet.sim.judge import bless, drift, inject, narrative, opinions
from ratchet.sim.judge.sections import prompt_name

__all__ = ["JSON_HANDLERS", "TEXT_HANDLERS", "UNKNOWN_PROMPT_REPLY", "respond", "prompt_text"]

JSON_HANDLERS: dict[str, Callable[[str], dict[str, object]]] = {
    "screen": opinions.screen,
    "check": opinions.check,
    "adjudicate": opinions.adjudicate,
    "extract_world_facts": bless.extract_world_facts,
    "infer_success_criteria": bless.infer_success_criteria,
    "classify_side_effect": bless.classify_side_effect,
    "compare_fact": drift.compare_fact,
    "attribute_drift": drift.attribute_drift,
    "inject_regression": inject.inject_regression,
}
TEXT_HANDLERS: dict[str, Callable[[str], str]] = {
    "narrative": narrative.narrative,
    "digest": narrative.digest,
}
UNKNOWN_PROMPT_REPLY = "The simulator has no heuristic for this prompt."


def prompt_text(messages: list[dict[str, object]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def respond(header: str | None, messages: list[dict[str, object]]) -> str:
    name = prompt_name(header)
    text = prompt_text(messages)
    if name in JSON_HANDLERS:
        return json.dumps(JSON_HANDLERS[name](text), sort_keys=True, ensure_ascii=False)
    if name in TEXT_HANDLERS:
        return TEXT_HANDLERS[name](text)
    return UNKNOWN_PROMPT_REPLY
