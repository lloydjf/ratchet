"""Offline dry run: drive the simulator the way MinimalRunner does, against a world in-process.

Development and test infrastructure (ADR 0018), not the recording path: goldens are
recorded through the real loop (MinimalRunner → proxy → sim or Token Factory → mock tools).
This module reproduces that loop without servers so the seeded scenarios can be checked in
milliseconds:

- the system prompt comes from ``ratchet.replay.harness.prompt.build_system_prompt`` with the
  MinimalRunner's own trigger matcher (``select_skills``);
- each model call is a ``POST /v1/chat/completions`` to the simulator app over ASGI, with the
  full catalog as tools, ``temperature=0`` and ``X-Ratchet-Purpose: agent``;
- mocked tools are answered by the world adapters (``ratchet.mocktools.adapters``) on the
  run's own world state; local ``fs.*`` tools run in an optional workspace directory;
- ``blocked(tool, args)`` stands in for a replay cassette miss on a hard-class tool: the call
  is answered with MinimalRunner's ``BLOCKED_RESULT`` and marked ``blocked``.

``python -m ratchet.sim.dryrun [--task-id ID]`` prints the simulated final outputs of the demo
goldens. Its output is simulated and must never be presented as Nemotron output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import yaml

from ratchet.errors import RatchetError
from ratchet.mocktools.adapters import get_adapter
from ratchet.mocktools.world import CallContext, World
from ratchet.replay.harness.local_tools import LocalTools
from ratchet.replay.harness.prompt import build_system_prompt
from ratchet.replay.harness.routing import BLOCKED_RESULT
from ratchet.replay.harness.skills import Skill, load_skills, select_skills
from ratchet.sim.agent.base import DEFAULT_NOW
from ratchet.tools.catalog import LOCAL_TOOLS, from_wire_name, openai_tools

__all__ = [
    "SCENARIOS_DIR",
    "ToolStep",
    "ModelStep",
    "DryRun",
    "Blocker",
    "run_task",
    "load_golden_tasks",
    "main",
]

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"
MAX_ITERATIONS = 25
Blocker = Callable[[str, dict[str, object]], bool]


@dataclass(frozen=True)
class ToolStep:
    step_id: str
    tool: str
    args: dict[str, object]
    result: dict[str, object]
    blocked: bool = False


@dataclass(frozen=True)
class ModelStep:
    step_id: str
    content: str
    tools_requested: list[str]
    usage: dict[str, object]


@dataclass
class DryRun:
    task: str
    skills_invoked: list[str]
    final_output: str = ""
    steps: list[ToolStep | ModelStep] = field(default_factory=list)
    responses: list[bytes] = field(default_factory=list)  # raw simulator bodies, in order

    @property
    def tool_steps(self) -> list[ToolStep]:
        return [s for s in self.steps if isinstance(s, ToolStep)]

    def calls(self, tool: str) -> list[ToolStep]:
        return [s for s in self.tool_steps if s.tool == tool]


def _execute(
    tool: str,
    args: dict[str, object],
    *,
    world: World,
    run_id: str,
    now: datetime,
    workspace: Path | None,
) -> dict[str, object]:
    if tool in LOCAL_TOOLS:
        if workspace is None:
            return {"error": "no workspace for local tools"}
        return LocalTools(workspace).call(tool, args)
    adapter = get_adapter(tool)
    if adapter is None:
        return {"error": "unknown tool"}
    try:
        return adapter(world.state(run_id), args, CallContext(run_id, now))
    except RatchetError as exc:
        return {"error": f"tool failed with status {exc.http_status}"}


async def _chat(client: httpx.AsyncClient, messages: list[dict[str, object]], tier: str) -> bytes:
    body = {
        "model": f"ratchet-sim/{tier}",
        "messages": messages,
        "tools": openai_tools(),
        "tool_choice": "auto",
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 1200,
    }
    response = await client.post(
        "/v1/chat/completions", json=body, headers={"X-Ratchet-Purpose": "agent"}
    )
    response.raise_for_status()
    return response.content


async def run_task(
    client: httpx.AsyncClient,
    task: str,
    skills: list[Skill],
    *,
    world: World,
    run_id: str,
    now: datetime = DEFAULT_NOW,
    tier: str = "super",
    workspace: Path | None = None,
    blocked: Blocker | None = None,
) -> DryRun:
    """Run one task to its final answer (or the 25-call cap) against the simulator."""
    chosen = select_skills(task, skills)
    run = DryRun(task=task, skills_invoked=[s.name for s in chosen])
    messages: list[dict[str, object]] = [
        {"role": "system", "content": build_system_prompt(skills, chosen, now)},
        {"role": "user", "content": task},
    ]
    n_tools = 0
    for n_model in range(1, MAX_ITERATIONS + 1):
        raw = await _chat(client, messages, tier)
        run.responses.append(raw)
        payload = json.loads(raw)
        message = payload["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        content = message.get("content") or ""
        names = [from_wire_name(c["function"]["name"]) for c in calls]
        run.steps.append(ModelStep(f"m{n_model:03d}", content, names, payload["usage"]))
        assistant: dict[str, object] = {"role": "assistant", "content": message.get("content")}
        if calls:
            assistant["tool_calls"] = calls
        messages.append(assistant)
        if not calls:
            run.final_output = content
            return run
        for call, tool in zip(calls, names, strict=True):
            n_tools += 1
            args = json.loads(call["function"]["arguments"] or "{}")
            stop = blocked is not None and blocked(tool, args)
            result = (
                dict(BLOCKED_RESULT)
                if stop
                else _execute(tool, args, world=world, run_id=run_id, now=now, workspace=workspace)
            )
            run.steps.append(ToolStep(f"t{n_tools:03d}", tool, args, result, stop))
            text = json.dumps(result, sort_keys=True, ensure_ascii=False)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
    return run


def load_golden_tasks(scenarios: Path = SCENARIOS_DIR) -> list[dict[str, object]]:
    data = yaml.safe_load((scenarios / "golden" / "tasks.yaml").read_text(encoding="utf-8"))
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []


async def _main(task_id: str | None, skills_dir: Path, world_dir: Path) -> int:
    from ratchet.sim.app import create_app  # local: the CLI is the only caller needing it

    skills = load_skills(skills_dir)
    world = World.load(world_dir)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://ratchet-sim") as client:
        for task in load_golden_tasks():
            if task_id and task.get("task_id") != task_id:
                continue
            run = await run_task(
                client, str(task["task_input"]), skills, world=world, run_id=str(task["task_id"])
            )
            print(f"=== {task['task_id']} (simulated) skills={run.skills_invoked}")
            print(run.final_output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ratchet.sim.dryrun")
    parser.add_argument("--task-id")
    parser.add_argument("--skills", type=Path, default=SCENARIOS_DIR / "skills")
    parser.add_argument("--world", type=Path, default=SCENARIOS_DIR / "world")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.task_id, args.skills, args.world))


if __name__ == "__main__":
    raise SystemExit(main())
