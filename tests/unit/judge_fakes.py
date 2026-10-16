"""Test doubles and helpers for the judge suites (no proxy, no network).

``FakeModelClient`` replays scripted responses per prompt name (the ``<name>`` part of the
``X-Ratchet-Prompt`` header) and records every call. A scripted item is a reply text, a
``ChatResult``, an exception instance (raised), or a callable ``(FakeCall) -> str | ...``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from ratchet.config.pricing import cost_usd
from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.judge import level0
from ratchet.judge.cascade import PairContext
from ratchet.judge.structured import reset_structured_modes
from ratchet.llm.client import ChatResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TRACES_DIR = FIXTURES / "traces"
RESPONSES_DIR = FIXTURES / "model_responses"
TAVILY_DIR = FIXTURES / "tavily"


def _load_factory() -> ModuleType:
    name = "ratchet_test_trace_factory"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TRACES_DIR / "factory.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


factory = _load_factory()


@dataclass
class FakeCall:
    tier: str
    purpose: str
    messages: list[dict[str, object]]
    max_tokens: int
    response_format: dict[str, object] | None
    thinking: bool | None
    reasoning_budget: int | None
    prompt_name: str | None

    @property
    def prompt(self) -> str:
        return (self.prompt_name or "").split("@")[0]

    @property
    def user_text(self) -> str:
        users = [m for m in self.messages if m["role"] == "user"]
        return str(users[0]["content"]) if users else ""

    @property
    def system_text(self) -> str:
        return "\n".join(str(m["content"]) for m in self.messages if m["role"] == "system")


Scripted = str | ChatResult | BaseException | Callable[[FakeCall], Any]


@dataclass
class FakeModelClient:
    script: dict[str, list[Scripted]] = field(default_factory=dict)
    default: Scripted | None = None
    calls: list[FakeCall] = field(default_factory=list)

    def add(self, prompt: str, *items: Scripted) -> FakeModelClient:
        self.script.setdefault(prompt, []).extend(items)
        return self

    def calls_for(self, prompt: str) -> list[FakeCall]:
        return [c for c in self.calls if c.prompt == prompt]

    @property
    def tiers(self) -> list[str]:
        return [c.tier for c in self.calls]

    async def chat(self, **kw: Any) -> ChatResult:
        call = FakeCall(
            tier=kw["tier"],
            purpose=kw["purpose"],
            messages=kw["messages"],
            max_tokens=kw["max_tokens"],
            response_format=kw.get("response_format"),
            thinking=kw.get("thinking"),
            reasoning_budget=kw.get("reasoning_budget"),
            prompt_name=kw.get("prompt_name"),
        )
        self.calls.append(call)
        queue = self.script.get(call.prompt) or []
        item = queue.pop(0) if queue else self.default
        if item is None:
            raise AssertionError(f"no scripted response for prompt {call.prompt!r}")
        if callable(item) and not isinstance(item, BaseException):
            item = item(call)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, ChatResult):
            return item
        return _result(call, str(item))


def _result(call: FakeCall, text: str) -> ChatResult:
    prompt_tokens = sum(len(str(m["content"])) for m in call.messages) // 4
    completion_tokens = max(len(text) // 4, 1)
    return ChatResult(
        text=text,
        tier=call.tier,  # type: ignore[arg-type]
        model=f"ratchet-sim/{call.tier}",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=cost_usd(call.tier, prompt_tokens, completion_tokens),  # type: ignore[arg-type]
        step_id=f"m{len(call.messages):03d}",
    )


def opinion(
    verdict: str,
    confidence: float,
    reasons: list[str] | None = None,
    evidence: list[str] | None = None,
    **extra: object,
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "reasons": reasons or [f"{verdict} per fixture"],
            "evidence_steps": evidence or [],
            **extra,
        }
    )


def pair_context(pair: Any, *, skill_diff: str | None = None) -> PairContext:
    return PairContext(
        golden=pair.golden,
        replay=pair.replay,
        unexpected=list(pair.unexpected),
        skill_diff=pair.skill_diff if skill_diff is None else skill_diff,
        level0=level0.diff(pair.golden, pair.replay, list(pair.unexpected)),
    )


def responses(prompt: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((RESPONSES_DIR / f"{prompt}.json").read_text("utf-8"))
    return data


@contextmanager
def settings(tmp_path: Path, **overrides: object) -> Iterator[Settings]:
    reset_structured_modes()
    configured = configure(
        Settings(ratchet_data_dir=tmp_path / "data", ratchet_log_level="ERROR", **overrides)  # type: ignore[arg-type]
    )
    try:
        yield configured
    finally:
        reset_settings()
        reset_structured_modes()
