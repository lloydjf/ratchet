"""Runs captured from an unmodified external harness.

Any OpenAI-compatible harness pointed at the proxy (``OPENAI_BASE_URL=http://127.0.0.1:8081/v1``;
run id from ``X-Ratchet-Run-Id``, the path form, or generated) leaves
``traces/<run_id>/steps.llm.jsonl`` (and ``steps.tool.jsonl`` if it used the mock tools) but
no ``trace.json``: nobody called :func:`ratchet.capture.runs.finalize_trace`. This module
lists such runs and finalizes one into a ``live`` trace so it can be blessed:

- ``task_input``: the first user message of the first recorded request;
- ``final_output``: the last non-empty assistant text;
- ``skills_invoked``: best effort, the ``## Skill: <name>`` headers in the system prompts;
- the workspace is unknown, so there are no artifacts and no workspace-diff steps.

Only runs whose model calls are all ``purpose=agent`` qualify (judge, bless and digest
calls also leave step files) and replay runs (``run.json`` with an ``edit_id``) never do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ratchet.capture.runs import finalize_trace, read_run_meta
from ratchet.errors import NotFoundError
from ratchet.models import EnvSnapshot, LLMCall, Step, Trace
from ratchet.proxy.recorder import assistant_text
from ratchet.store import Store

__all__ = [
    "CapturedRun",
    "EXTERNAL_HARNESS",
    "is_captured",
    "captured_runs",
    "finalize_captured",
    "skills_from_prompt",
]

EXTERNAL_HARNESS = "external/unknown"
_STEP_FILES = ("steps.llm.jsonl", "steps.tool.jsonl")
_SKILL_HEADER = re.compile(r"^## Skill: ([a-z0-9]+(?:-[a-z0-9]+)*)\s*$", re.M)
_PREVIEW = 200


@dataclass(frozen=True)
class CapturedRun:
    run_id: str
    task_preview: str
    n_steps: int
    agent_tier: str
    last_step_at: datetime


def _text(content: object) -> str:
    """Chat ``content``: a string or a list of ``{"type": "text", "text": ...}`` parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text") for p in content if isinstance(p, dict)]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def _messages(body: object) -> list[dict[str, object]]:
    if not isinstance(body, dict):
        return []
    raw = body.get("messages", body.get("input"))
    if isinstance(raw, str):
        return [{"role": "user", "content": raw}]
    return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []


def skills_from_prompt(text: str) -> list[str]:
    return sorted(set(_SKILL_HEADER.findall(text)))


def _llm_steps(steps: list[Step]) -> list[LLMCall]:
    return [s for s in steps if isinstance(s, LLMCall)]


def is_captured(store: Store, run_id: str) -> bool:
    """A run dir with agent model calls and no ``trace.json`` (and not a replay)."""
    root = store.trace_dir(run_id)
    if (root / "trace.json").exists() or not any((root / n).is_file() for n in _STEP_FILES):
        return False
    meta = read_run_meta(store, run_id)
    if meta is not None and meta.edit_id is not None:
        return False
    try:
        calls = _llm_steps(store.collect_steps(run_id))
    except (OSError, ValueError):
        return False
    return bool(calls) and all(c.purpose == "agent" for c in calls)


def _first_user(store: Store, run_id: str, calls: list[LLMCall]) -> str:
    for call in calls:
        body = _payload(store, run_id, call.messages_ref)
        for message in _messages(body):
            if message.get("role") == "user":
                return _text(message.get("content"))
    return ""


def _payload(store: Store, run_id: str, ref: str) -> object:
    try:
        return store.read_step_payload(run_id, ref)
    except (OSError, ValueError):
        return None


def captured_runs(store: Store, *, limit: int = 50) -> list[CapturedRun]:
    """Captured, not yet finalized runs, newest activity first."""
    root = store.data_dir / "traces"
    if not root.is_dir():
        return []
    found: list[CapturedRun] = []
    for path in root.iterdir():
        if not path.is_dir() or not is_captured(store, path.name):
            continue
        steps = store.collect_steps(path.name)
        calls = _llm_steps(steps)
        found.append(
            CapturedRun(
                run_id=path.name,
                task_preview=_first_user(store, path.name, calls)[:_PREVIEW],
                n_steps=len(steps),
                agent_tier=calls[0].tier,
                last_step_at=max(c.started_at for c in calls),
            )
        )
    found.sort(key=lambda r: (r.last_step_at, r.run_id), reverse=True)
    return found[:limit]


def _final_output(store: Store, run_id: str, calls: list[LLMCall]) -> str:
    for call in reversed(calls):
        body = _payload(store, run_id, call.response_ref)
        if isinstance(body, dict):
            text = assistant_text(body, call.api)
            if text.strip():
                return text
    return ""


def _skills(store: Store, run_id: str, calls: list[LLMCall]) -> list[str]:
    names: set[str] = set()
    for call in calls:
        for message in _messages(_payload(store, run_id, call.messages_ref)):
            if message.get("role") in ("system", "developer"):
                names.update(skills_from_prompt(_text(message.get("content"))))
    return sorted(names)


def _seed(store: Store, run_id: str, call: LLMCall) -> int:
    body = _payload(store, run_id, call.messages_ref)
    seed = body.get("seed") if isinstance(body, dict) else None
    return seed if isinstance(seed, int) else 0


def _label(task: str) -> str:
    text = " ".join(task.split())
    return text if len(text) <= 60 else text[:59] + "…"


def finalize_captured(store: Store, run_id: str, *, skills_commit: str = "unknown") -> Trace:
    """Turn a captured run into a saved ``live`` trace (NotFoundError if it is not one)."""
    if not is_captured(store, run_id):
        raise NotFoundError(f"run {run_id} has no captured agent steps", code="not_found")
    calls = _llm_steps(store.collect_steps(run_id))
    task = _first_user(store, run_id, calls)
    env = EnvSnapshot(
        workspace_ref="",
        frozen_time=calls[0].started_at,
        harness_version=EXTERNAL_HARNESS,
        skills_commit=skills_commit,
    )
    return finalize_trace(
        store,
        run_id,
        task_input=task,
        final_output=_final_output(store, run_id, calls),
        kind="live",
        env=env,
        agent_tier=calls[0].tier,
        seed=_seed(store, run_id, calls[0]),
        skills_invoked=_skills(store, run_id, calls),
        workspace_before=None,
        workspace_after=None,
        task_label=_label(task),
    )
