"""Simulated bless-time prompts: ``extract_world_facts``, ``infer_success_criteria``,
``classify_side_effect`` (design doc §4.2, ADR 0018). Test infrastructure, never model output.

- facts: exchange rates from ``finance.*`` tool results (value as the output printed it,
  e.g. ``1.0800``) and ``1 EUR = 1.0800 USD`` / ``EUR/USD … 1.08`` mentions in the output;
  versions and dollar prices only from ``web.*`` results (receipt prices are not world facts);
- criteria: one per distinct ``Key: value`` key in the output (``The output includes the
  <Key> line``) and one per tool action (``<tool> is called``);
- side effects: the tool catalog default, else a name heuristic (unknown → conservative
  ``write_irreversible``).
"""

from __future__ import annotations

import re

from ratchet.sim.judge.sections import parse_sections
from ratchet.sim.text import content_lines, first_sentence, fixed, kv
from ratchet.tools.catalog import get_tool

__all__ = [
    "extract_world_facts",
    "infer_success_criteria",
    "classify_side_effect",
    "NAME_RULES",
]

_RESULT_LINE = re.compile(r"^([a-z]\d{3,}) ([a-z_]+\.[a-z_]+): (.*)$", re.MULTILINE)
_JSON_STR = r"\"{key}\":\s*\"([^\"]+)\""
_RATE = re.compile(r"\"rate\":\s*(\d+(?:\.\d+)?)")
_OUTPUT_RATE = re.compile(r"\b1 ([A-Z]{3}) = (\d+\.\d+) ([A-Z]{3})\b")
_PAIR_RATE = re.compile(r"\b([A-Z]{3})/([A-Z]{3})\b[^\n\d]{0,40}(\d+\.\d+)")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_VERSION = re.compile(r"\b(?:version\s+|v)(\d+\.\d+(?:\.\d+)?)\b", re.IGNORECASE)
_PRICE = re.compile(r"\$\s?(\d+(?:\.\d+)?)")
_TOOL = re.compile(r"\b([a-z_]+\.[a-z_]+)\b")
NAME_RULES: tuple[tuple[str, str], ...] = (
    (r"delete|remove|drop|purge|wipe|overwrite", "destructive"),
    (r"pay|charge|purchase|buy|transfer|refund|order", "spend"),
    (r"send|email|mail|post|notify|message|sms|tweet|publish", "external_send"),
    (r"close|create|merge|comment|invite|book|schedule|open_pr|release", "write_irreversible"),
    (r"label|tag|append|update|set|write|edit|add|rename|move", "write_reversible"),
    (r"get|list|read|search|fetch|parse|query|find|rate|log|now|lookup|freebusy", "read"),
)


def _field(text: str, key: str) -> str | None:
    match = re.search(_JSON_STR.format(key=key), text)
    return match.group(1) if match else None


def _fx_fact(step: str, preview: str, output: str) -> dict[str, object] | None:
    base, quote, rate = _field(preview, "base"), _field(preview, "quote"), _RATE.search(preview)
    if not (base and quote and rate):
        return None
    day = _field(preview, "date") or ""
    printed = re.search(rf"\b1 {base} = (\d+\.\d+) {quote}\b", output)
    value = printed.group(1) if printed else fixed(float(rate.group(1)), 4)
    return _fx(base, quote, value, day, step)


def _fx(base: str, quote: str, value: str, day: str, step: str) -> dict[str, object]:
    when = f" on {day}" if day else ""
    return {
        "fact_id": f"fx_{base.lower()}_{quote.lower()}",
        "statement": f"The {base}/{quote} exchange rate (1 {base} = {value} {quote}{when})",
        "source_step": step,
        "volatile": True,
        "query_hint": f"{base} to {quote} exchange rate today",
        "value": value,
    }


def _web_facts(step: str, preview: str, task: str) -> list[dict[str, object]]:
    topic = " ".join(task.split()[:8])
    facts: list[dict[str, object]] = []
    if version := _VERSION.search(preview):
        facts.append(
            {
                "fact_id": f"version_{step}",
                "statement": f"The source reported version {version.group(1)}.",
                "source_step": step,
                "volatile": True,
                "query_hint": f"{topic} latest version",
                "value": version.group(1),
            }
        )
    if price := _PRICE.search(preview):
        facts.append(
            {
                "fact_id": f"price_{step}",
                "statement": f"The source reported a price of ${price.group(1)}.",
                "source_step": step,
                "volatile": True,
                "query_hint": f"{topic} price",
                "value": price.group(1),
            }
        )
    return facts


def _output_rates(output: str, task: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for line in content_lines(output):
        match = _OUTPUT_RATE.search(line)
        pair = (match.group(1), match.group(3), match.group(2)) if match else None
        if pair is None and (alt := _PAIR_RATE.search(line)):
            pair = (alt.group(1), alt.group(2), alt.group(3))
        if pair is None:
            continue
        day = _DATE.search(line) or _DATE.search(task)
        found.append(_fx(pair[0], pair[1], pair[2], day.group(1) if day else "", ""))
    return found


def extract_world_facts(text: str) -> dict[str, object]:
    s = parse_sections("extract_world_facts", text)
    output, task = s.get("final_output", ""), s.get("task_input", "")
    facts: list[dict[str, object]] = []
    for step, tool, preview in _RESULT_LINE.findall(s.get("tool_results", "")):
        if tool.startswith("finance.") and (fact := _fx_fact(step, preview, output)):
            facts.append(fact)
        elif tool.startswith("web."):
            facts += _web_facts(step, preview, task)
    facts += _output_rates(output, task)
    unique: dict[str, dict[str, object]] = {}
    for fact in facts:
        unique.setdefault(str(fact["fact_id"]), fact)
    return {"facts": list(unique.values())}


def infer_success_criteria(text: str) -> dict[str, object]:
    s = parse_sections("infer_success_criteria", text)
    keys = [p[0] for line in content_lines(s.get("final_output", "")) if (p := kv(line))]
    criteria = [f"The output includes the {k} line" for k in dict.fromkeys(keys)][:4]
    artifacts = s.get("artifact_list", "")
    criteria += [f"{t} is called" for t in dict.fromkeys(_TOOL.findall(artifacts))][:2]
    if not criteria:
        task = first_sentence(s.get("task_input", "")) or "the task"
        criteria = [f"The output answers the task: {task[:120]}"]
    return {"criteria": criteria[:6]}


def classify_side_effect(text: str) -> dict[str, object]:
    s = parse_sections("classify_side_effect", text)
    tool = s.get("tool", "").strip()
    spec = get_tool(tool)
    if spec is not None:
        return {"side_effect": spec.side_effect, "reason": f"Catalog default for {tool}."}
    leaf = tool.rsplit(".", 1)[-1].lower()
    for pattern, cls in NAME_RULES:
        if re.search(rf"(?:^|_)(?:{pattern})(?:_|$)", leaf):
            return {"side_effect": cls, "reason": f"The tool name suggests {cls}."}
    return {
        "side_effect": "write_irreversible",
        "reason": "Unknown tool; treated conservatively as an irreversible write.",
    }
