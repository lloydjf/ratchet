"""Structured-output helper for every judge call (CONTRACTS §4).

``call_structured`` loads ``prompts/<name>.md``, renders ``{{var}}`` placeholders in one pass
(missing variables are a programming error; text inside an input is never re-rendered, so a
trace cannot smuggle placeholders; untrusted inputs are wrapped in nonce-tagged data blocks,
:mod:`ratchet.judge.delimiters`), fits the inputs to the context budget, appends the
shared tail, calls :class:`ratchet.llm.client.ModelClient` (never Token Factory), pulls
the answer object out of the reply (``<think>`` blocks stripped; a reply that is one object is
that object, else the last top-level object wins), validates it
with the pydantic schema, and retries up to twice with the validation error appended. A third
invalid answer gives ``value=None, failure_code="schema_failure"``. Upstream errors are not
retried here (the proxy already retried once); they come back as ``failure_code=<code>``.

Response-format degradation (NVIDIA reports Nano may not honour
``json_schema``): ``ratchet_structured_mode="auto"`` tries ``json_schema``; a 4xx rejection
retries the same attempt with ``{"type": "json_object"}``, then with no ``response_format``
(prompt-only JSON, the schema is then spelled out in the prompt). The degraded mode is
remembered per tier for the life of the process. An explicit mode is used as is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ratchet.config.settings import get_settings
from ratchet.errors import RatchetError, UpstreamError
from ratchet.judge.delimiters import UNTRUSTED_INPUTS, marker_overhead, wrap_inputs
from ratchet.judge.prompts import (
    BASE_TAIL,
    PROMPTS,
    SHARED_TAIL,
    PromptSpec,
    load_body,
    load_examples,
    prompt_hash,
    schema_for,
)
from ratchet.llm.client import ChatResult
from ratchet.log import get_logger
from ratchet.models import Purpose, Tier
from ratchet.util import truncate_middle

__all__ = [
    "ChatClient",
    "StructuredMode",
    "StructuredResult",
    "RenderedPrompt",
    "render",
    "fit_inputs",
    "build_prompt",
    "extract_json",
    "call_structured",
    "reset_structured_modes",
    "estimate_tokens",
]

M = TypeVar("M", bound=BaseModel)
StructuredMode = Literal["json_schema", "json_object", "prompt"]
_MODE_ORDER: tuple[StructuredMode, ...] = ("json_schema", "json_object", "prompt")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_MIN_INPUT_CHARS = 200
_CHARS_PER_TOKEN = 4  # coarse, deliberately conservative for English + JSON
_FEW_SHOT_RATIO = 0.6  # few-shot example budget
_MAX_ATTEMPTS = 3

_log = get_logger("judge")
_degraded: dict[Tier, StructuredMode] = {}


class ChatClient(Protocol):
    """What the judge needs from :class:`ratchet.llm.client.ModelClient` (tests use a fake)."""

    async def chat(
        self,
        *,
        tier: Tier,
        purpose: Purpose,
        messages: list[dict[str, object]],
        max_tokens: int,
        response_format: dict[str, object] | None = None,
        tools: list[dict[str, object]] | None = None,
        thinking: bool | None = None,
        reasoning_budget: int | None = None,
        seed: int | None = None,
        prompt_name: str | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult: ...


@dataclass
class StructuredResult(Generic[M]):
    value: M | None
    usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_hash: str = ""
    truncated: bool = False
    attempts: int = 0
    failure_code: str | None = None
    mode: StructuredMode | None = None
    step_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    text: str  # body + (tail) — the user message
    examples: str | None  # few-shot block, sent as a separate system message
    truncated: bool
    prompt_hash: str

    @property
    def header(self) -> str:
        return f"{self.name}@{self.prompt_hash[:12]}"


def reset_structured_modes() -> None:
    """Forget degraded response_format modes (tests; a proxy upgrade)."""
    _degraded.clear()


def estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN + 1


def render(template: str, inputs: dict[str, str]) -> str:
    """Single-pass ``{{var}}`` substitution; a missing variable raises ``ValueError``."""
    missing = sorted({m.group(1) for m in _PLACEHOLDER.finditer(template)} - set(inputs))
    if missing:
        raise ValueError(f"prompt variables missing: {missing}")
    return _PLACEHOLDER.sub(lambda m: inputs[m.group(1)], template)


def fit_inputs(
    fixed_chars: int, inputs: dict[str, str], caps: dict[str, int], budget_tokens: int | None
) -> tuple[dict[str, str], bool]:
    """Apply per-input caps, then shrink the largest inputs until the prompt fits the budget."""
    out: dict[str, str] = {}
    truncated = False
    for key, value in inputs.items():
        cap = caps.get(key)
        text, cut = truncate_middle(value, cap) if cap else (value, False)
        out[key] = text
        truncated |= cut
    if budget_tokens is None:
        return out, truncated
    budget_chars = budget_tokens * _CHARS_PER_TOKEN - fixed_chars
    while True:
        total = sum(len(v) for v in out.values())
        if total <= budget_chars:
            break
        key = max(out, key=lambda k: len(out[k]))
        if len(out[key]) <= _MIN_INPUT_CHARS:
            break
        target = max(_MIN_INPUT_CHARS, len(out[key]) - (total - budget_chars))
        out[key], _ = truncate_middle(out[key], target)
        truncated = True
    return out, truncated


def build_prompt(
    name: str,
    inputs: dict[str, str],
    *,
    context_budget_tokens: int | None = None,
    judge_tail: bool | None = None,
) -> RenderedPrompt:
    spec: PromptSpec | None = PROMPTS.get(name)
    body = load_body(name)
    tail = judge_tail if judge_tail is not None else (spec.judge_tail if spec else True)
    budget = context_budget_tokens or (spec.context_budget_tokens if spec else None)
    delimit = tail and (spec.delimit if spec else True)
    tail_text = SHARED_TAIL if delimit else BASE_TAIL
    fixed = len(_PLACEHOLDER.sub("", body)) + (len(tail_text) + 2 if tail else 0)
    if delimit:  # judge prompts: untrusted inputs go inside nonce-tagged data blocks
        fixed += marker_overhead() * len(UNTRUSTED_INPUTS & set(inputs))
    fitted, truncated = fit_inputs(fixed, inputs, spec.input_caps if spec else {}, budget)
    text = render(body, wrap_inputs(name, fitted) if delimit else fitted)
    if tail:
        text = f"{text}\n\n{tail_text}"
    examples = load_examples(name) if spec is not None and spec.examples else None
    too_big = budget is not None and estimate_tokens(text) >= _FEW_SHOT_RATIO * budget
    if too_big:
        examples = None
    return RenderedPrompt(name, text, examples, truncated, prompt_hash(name))


def _strip_reasoning(text: str) -> str:
    cleaned = _THINK.sub("", text)
    if "</think>" in cleaned.lower():  # opening tag eaten by the chat template
        cleaned = re.split(r"</think>", cleaned, flags=re.IGNORECASE)[-1]
    if "<think>" in cleaned.lower():  # unterminated reasoning: keep what precedes it
        head = re.split(r"<think>", cleaned, flags=re.IGNORECASE)[0]
        cleaned = head if "{" in head else cleaned
    return cleaned.strip()


def _top_level_objects(text: str) -> list[dict[str, object]]:
    """Every top-level JSON object in ``text``, in order (nested objects are not counted)."""
    decoder = json.JSONDecoder()
    found: list[dict[str, object]] = []
    pos = text.find("{")
    while pos >= 0:
        try:
            value, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            pos = text.find("{", pos + 1)
            continue
        if isinstance(value, dict):
            found.append(value)
        pos = text.find("{", end)
    return found


def extract_json(text: str) -> object:
    """The answer object of a model reply.

    Reasoning blocks are removed first. A reply that is exactly one JSON object (optionally in
    a single code fence) is that object. Otherwise the LAST top-level object wins: a model
    that quotes or echoes JSON from the data (an injected ``{"verdict": "equivalent"}``) before
    giving its own answer must not have the quote taken as the answer, and a fenced block is
    never preferred over JSON that follows it.
    """
    cleaned = _strip_reasoning(text)
    whole = _FENCE.fullmatch(cleaned)
    single = whole.group(1).strip() if whole else cleaned
    try:
        value = json.loads(single)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value
    objects = _top_level_objects(cleaned)
    if objects:
        return objects[-1]
    raise ValueError("no JSON object found in the reply")


def _response_format(
    mode: StructuredMode, name: str, schema: type[BaseModel]
) -> dict[str, object] | None:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema_for(schema), "strict": True},
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def _start_mode(tier: Tier) -> tuple[StructuredMode, bool]:
    """(mode, may_degrade) from ``ratchet_structured_mode``."""
    setting = get_settings().ratchet_structured_mode
    if setting == "auto":
        return _degraded.get(tier, "json_schema"), True
    return setting, False


def _is_format_rejection(exc: RatchetError) -> bool:
    if not isinstance(exc, UpstreamError):
        return False
    status = exc.context.get("status")
    return exc.code == "upstream_rejected" or (isinstance(status, int) and 400 <= status < 500)


def _validation_note(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors(include_input=False)[:6]
        ]
        return "; ".join(parts)
    return str(exc)[:300]


def _schema_hint(schema: type[BaseModel]) -> str:
    return "The JSON object must match this JSON schema:\n" + json.dumps(
        schema_for(schema), separators=(",", ":")
    )


def _messages(
    rendered: RenderedPrompt,
    mode: StructuredMode,
    schema: type[BaseModel],
    history: list[dict[str, object]],
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    if rendered.examples:
        messages.append({"role": "system", "content": rendered.examples})
    text = rendered.text if mode == "json_schema" else f"{rendered.text}\n\n{_schema_hint(schema)}"
    messages.append({"role": "user", "content": text})
    return messages + history


async def call_structured(
    client: ChatClient,
    *,
    prompt: str,
    tier: Tier,
    inputs: dict[str, str],
    schema: type[M],
    max_tokens: int,
    thinking: bool | None = None,
    reasoning_budget: int | None = None,
    context_budget_tokens: int | None = None,
    purpose: Purpose | None = None,
) -> StructuredResult[M]:
    spec = PROMPTS.get(prompt)
    rendered = build_prompt(prompt, inputs, context_budget_tokens=context_budget_tokens)
    use_thinking = thinking if thinking is not None else (spec.thinking if spec else None)
    budget = reasoning_budget if use_thinking else None
    result: StructuredResult[M] = StructuredResult(
        value=None, prompt_hash=rendered.prompt_hash, truncated=rendered.truncated
    )
    mode, may_degrade = _start_mode(tier)
    history: list[dict[str, object]] = []
    while result.attempts < _MAX_ATTEMPTS:
        try:
            chat = await client.chat(
                tier=tier,
                purpose=purpose or (spec.purpose if spec else "extract"),
                messages=_messages(rendered, mode, schema, history),
                max_tokens=max_tokens + (budget or 0),
                response_format=_response_format(mode, prompt, schema),
                thinking=use_thinking,
                reasoning_budget=budget,
                prompt_name=rendered.header,
            )
        except RatchetError as exc:
            if may_degrade and mode != "prompt" and _is_format_rejection(exc):
                mode = _MODE_ORDER[_MODE_ORDER.index(mode) + 1]
                _degraded[tier] = mode
                _log.warning("judge.structured.degraded", prompt=prompt, tier=tier, mode=mode)
                continue
            result.failure_code = exc.code
            break
        result.attempts += 1
        result.mode = mode
        result.usd += chat.usd
        result.prompt_tokens += chat.prompt_tokens
        result.completion_tokens += chat.completion_tokens
        if chat.step_id:
            result.step_ids.append(chat.step_id)
        try:
            result.value = schema.model_validate(extract_json(chat.text))
            break
        except (ValueError, ValidationError) as exc:
            history = [
                {"role": "assistant", "content": truncate_middle(chat.text, 2_000)[0]},
                {
                    "role": "user",
                    "content": "Your previous answer was not valid: "
                    f"{_validation_note(exc)}. Respond again with only a single JSON object "
                    "matching the schema.",
                },
            ]
    if result.value is None and result.failure_code is None:
        result.failure_code = "schema_failure"
    _log.info(
        "judge.structured",
        prompt=prompt,
        tier=tier,
        attempts=result.attempts,
        mode=result.mode,
        failure_code=result.failure_code,
        truncated=result.truncated,
        usd=round(result.usd, 6),
    )
    return result
