"""MinimalRunner: the in-repo fallback harness (ADR 0012).

A deliberately small agent loop that executes agentskills.io skills:

1. loads every valid skill under ``skills_dir``; the system prompt (pinned format, built by
   the pure :func:`build_system_prompt` in ``ratchet.replay.harness.prompt`` because the
   offline simulator parses it) carries ``Current date:``, the ``Available skills:`` index
   and a ``## Skill: <name>`` section for each of up to 3 skills whose trigger text matches
   the task (``ratchet.replay.harness.skills``: injectable matcher, lexical cosine by
   default, calibrated threshold 0.25 — see that module for why not 0.6);
2. exposes the whole tool catalog as OpenAI functions: mocked tools are POSTed to
   ``<mock>/tools/<tool>``; ``fs.*``/``shell.run`` run locally against the workspace
   (reads reported to ``/tools/<tool>/notify``; writes reconciled by the workspace diff);
   ``time.now`` answers the frozen time when one is configured, else goes to the mock;
3. runs the tool-calling loop against ``<proxy>/r/<run_id>/chat/completions`` (plus the
   ``X-Ratchet-Run-Id`` header) with ``temperature=0``, ``seed``, ``max_tokens=1200``, at
   most 25 model calls; a 409 ``ratchet_blocked`` becomes a tool result telling the model
   the action is not permitted, so it cannot "succeed" through a forbidden action;
4. reports ``skills_invoked`` = the skills whose text was injected.

Hitting the iteration cap is *not* a harness failure: the last assistant text is the final
output, so a skill edit that makes the agent loop is judged as a regression, not as infra.

CLI (runs inside a sandbox): ``python -m ratchet.replay.harness.minimal --task-file ...
--skills ... --workspace ... --proxy ... --mock ... --run-id ... --seed ... --tier ...
--out result.json``; exit 0 ok, 5 harness failure, 6 budget exceeded.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

import ratchet
from ratchet import clock
from ratchet.errors import BudgetExceeded, HarnessError
from ratchet.llm.client import HEADER
from ratchet.log import get_logger
from ratchet.models import TIERS, HarnessResult, Tier
from ratchet.replay.harness.base import (
    DUMMY_KEY,
    EXIT_BUDGET,
    EXIT_HARNESS,
    EXIT_OK,
    HarnessConfig,
    HostPaths,
    SandboxPaths,
    SandboxPlan,
)
from ratchet.replay.harness.prompt import build_system_prompt
from ratchet.replay.harness.routing import BLOCKED_RESULT, ToolRouter, error_code
from ratchet.replay.harness.skills import (
    LEXICAL_THRESHOLD,
    Skill,
    SkillMatcher,
    lexical_matcher,
    load_skills,
    select_skills,
)
from ratchet.store.archive import pack_dir
from ratchet.tools.catalog import from_wire_name, openai_tools

__all__ = ["MinimalRunner", "BLOCKED_RESULT", "VERSION", "build_system_prompt", "main"]

VERSION = f"minimal/{ratchet.__version__}"
MAX_ITERATIONS = 25
MAX_TOKENS = 1200
_REQUEST_TIMEOUT_S = 120.0
_log = get_logger("replay.harness")

JSON = dict[str, Any]  # Any: decoded JSON from the model API


class MinimalRunner:
    name = "minimal"

    def __init__(
        self,
        *,
        matcher: SkillMatcher = lexical_matcher,
        threshold: float = LEXICAL_THRESHOLD,
        transport: httpx.BaseTransport | None = None,
        max_iterations: int = MAX_ITERATIONS,
        log_dir: Path | None = None,
    ) -> None:
        self._matcher = matcher
        self._threshold = threshold
        self._transport = transport
        self._max_iterations = max_iterations
        self._log_dir = log_dir
        self._cfg: HarnessConfig | None = None
        self._skills: list[Skill] = []
        self._events: list[dict[str, object]] = []
        self.last_error_code: str | None = None

    # -- adapter interface -------------------------------------------------------------------
    def configure(
        self,
        *,
        proxy_url: str,
        mock_tools_url: str,
        skills_dir: Path,
        workspace: Path,
        frozen_time: datetime | None,
        seed: int | None,
        agent_tier: Tier,
        run_id: str,
    ) -> None:
        self._cfg = HarnessConfig(
            proxy_url.rstrip("/"),
            mock_tools_url.rstrip("/"),
            skills_dir,
            workspace,
            frozen_time,
            seed,
            agent_tier,
            run_id,
        )
        self.reload_skills()

    @property
    def config(self) -> HarnessConfig:
        if self._cfg is None:
            raise HarnessError("MinimalRunner.configure() was not called", code="harness_failed")
        return self._cfg

    def loaded_skills(self) -> list[str]:
        return [skill.name for skill in self._skills]

    def reload_skills(self) -> None:
        self._skills = load_skills(self.config.skills_dir)

    def pre_task_snapshot(self) -> Path:
        dest = self._logs() / f"{self.config.run_id}.workspace.tar.zst"
        return pack_dir(self.config.workspace, dest)

    def run_task(self, task_input: str, *, timeout_s: int | float) -> HarnessResult:
        cfg = self.config
        self._events, self.last_error_code = [], None
        system, injected = self.system_prompt(task_input)
        messages: list[JSON] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task_input},
        ]
        final, ok = "", True
        with httpx.Client(transport=self._transport) as client:
            try:
                final = self._loop(client, messages, time.monotonic() + float(timeout_s))
            except (HarnessError, BudgetExceeded) as exc:
                ok, self.last_error_code = False, exc.code
                self._event("harness.error", code=exc.code)
        stdout_ref, stderr_ref = self._write_logs(cfg.run_id)
        _log.info("harness.run.done", run_id=cfg.run_id, ok=ok, n_events=len(self._events))
        return HarnessResult(
            final_output=final,
            exit_ok=ok,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            skills_invoked=injected,
            harness_version=VERSION,
        )

    # -- prompt -------------------------------------------------------------------------------
    def system_prompt(self, task_input: str) -> tuple[str, list[str]]:
        """The pinned prompt (:mod:`ratchet.replay.harness.prompt`) and the injected names."""
        chosen = select_skills(
            task_input, self._skills, matcher=self._matcher, threshold=self._threshold
        )
        now = self.config.frozen_time or clock.now()
        return build_system_prompt(self._skills, chosen, now), [skill.name for skill in chosen]

    # -- loop ---------------------------------------------------------------------------------
    def _loop(self, client: httpx.Client, messages: list[JSON], deadline: float) -> str:
        last_text = ""
        for _ in range(self._max_iterations):
            message = self._chat(client, messages, deadline)
            content = message.get("content")
            last_text = content if isinstance(content, str) else last_text
            calls = message.get("tool_calls") or []
            assistant: JSON = {"role": "assistant", "content": content}
            if calls:
                assistant["tool_calls"] = calls
            messages.append(assistant)
            if not calls:
                return last_text
            for call in calls:
                messages.append(self._tool_message(client, call))
        self._event("harness.iteration_cap", limit=self._max_iterations)
        return last_text

    def _chat(self, client: httpx.Client, messages: list[JSON], deadline: float) -> JSON:
        cfg = self.config
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HarnessError("harness wall clock exceeded", code="harness_timeout")
        body: JSON = {
            "model": cfg.agent_tier,  # the proxy rewrites this to the tier's model id
            "messages": messages,
            "tools": openai_tools(),
            "tool_choice": "auto",
            "temperature": 0,
            "top_p": 1,
            "max_tokens": MAX_TOKENS,
        }
        if cfg.seed is not None:
            body["seed"] = cfg.seed
        headers = {
            "Authorization": f"Bearer {DUMMY_KEY}",
            HEADER.RUN_ID: cfg.run_id,
            HEADER.PURPOSE: "agent",
            HEADER.TIER: cfg.agent_tier,
        }
        url = f"{cfg.proxy_url}/r/{cfg.run_id}/chat/completions"
        try:
            response = client.post(
                url, json=body, headers=headers, timeout=min(remaining, _REQUEST_TIMEOUT_S)
            )
        except httpx.HTTPError as exc:
            raise HarnessError("model call failed", code="upstream_unavailable") from exc
        self._event("model.call", status=response.status_code)
        if response.status_code >= 400:
            code = error_code(response) or "harness_failed"
            if code == "budget_exceeded":
                raise BudgetExceeded("proxy budget cap reached", code=code)
            raise HarnessError(f"model call returned {response.status_code}", code=code)
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise HarnessError("malformed model response", code="harness_failed") from exc
        if not isinstance(message, dict):
            raise HarnessError("malformed model response", code="harness_failed")
        return message

    def _tool_message(self, client: httpx.Client, call: JSON) -> JSON:
        function = call.get("function") or {}
        tool = from_wire_name(str(function.get("name", "")))
        try:
            args = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            args = None
        if not isinstance(args, dict):
            result: JSON = {"error": "tool arguments must be a JSON object"}
        else:
            router = ToolRouter(client, self.config, self._event)
            result = router.dispatch(tool, args)
        return {
            "role": "tool",
            "tool_call_id": str(call.get("id", "")),
            "content": json.dumps(result, sort_keys=True, ensure_ascii=False),
        }

    # -- logs ---------------------------------------------------------------------------------
    def _event(self, event: str, **fields: object) -> None:
        self._events.append({"event": event, **fields})

    def _logs(self) -> Path:
        if self._log_dir is None:
            self._log_dir = Path(tempfile.mkdtemp(prefix="ratchet-minimal-"))
        self._log_dir.mkdir(parents=True, exist_ok=True)
        return self._log_dir

    def _write_logs(self, run_id: str) -> tuple[str, str]:
        out = self._logs() / f"{run_id}.events.jsonl"
        err = self._logs() / f"{run_id}.errors.jsonl"
        out.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in self._events))
        errors = [e for e in self._events if e["event"] == "harness.error"]
        err.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in errors))
        return str(out), str(err)

    # -- sandboxed execution (worker) -----------------------------------------------------------
    def plan(
        self,
        task_input: str,
        *,
        timeout_s: float,
        host: HostPaths,
        inside: SandboxPaths,
        python: str,
    ) -> SandboxPlan:
        cfg = self.config
        (host.io / "task.txt").write_text(task_input, encoding="utf-8")
        argv = [python, "-I", "-m", "ratchet.replay.harness.minimal"]
        argv += ["--task-file", f"{inside.io}/task.txt", "--skills", inside.skills]
        argv += ["--workspace", inside.work, "--proxy", cfg.proxy_url]
        argv += ["--mock", cfg.mock_tools_url, "--run-id", cfg.run_id, "--tier", cfg.agent_tier]
        argv += [
            "--timeout",
            f"{max(timeout_s - 5.0, 1.0):.1f}",
            "--out",
            f"{inside.io}/result.json",
        ]
        if cfg.seed is not None:
            argv += ["--seed", str(cfg.seed)]
        if cfg.frozen_time is not None:
            argv += ["--frozen-time", clock.iso(cfg.frozen_time)]
        return SandboxPlan(argv=argv)

    def collect(
        self, exit_code: int, stdout: str, stderr: str, *, host: HostPaths
    ) -> HarnessResult:
        path = host.io / "result.json"
        try:
            result = HarnessResult.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return HarnessResult(
                final_output="",
                exit_ok=False,
                stdout_ref="",
                stderr_ref="",
                skills_invoked=[],
                harness_version=VERSION,
            )
        return result.model_copy(update={"exit_ok": result.exit_ok and exit_code == EXIT_OK})


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ratchet.replay.harness.minimal")
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--task")
    task.add_argument("--task-file", type=Path)
    for flag in ("--proxy", "--mock", "--run-id"):
        parser.add_argument(flag, required=True)
    parser.add_argument("--skills", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--frozen-time", type=clock.parse)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    task = args.task if args.task is not None else args.task_file.read_text(encoding="utf-8")
    runner = MinimalRunner(log_dir=args.out.parent)
    runner.configure(
        proxy_url=args.proxy,
        mock_tools_url=args.mock,
        skills_dir=args.skills,
        workspace=args.workspace,
        frozen_time=args.frozen_time,
        seed=args.seed,
        agent_tier=args.tier,
        run_id=args.run_id,
    )
    frozen = clock.frozen(args.frozen_time) if args.frozen_time else contextlib.nullcontext()
    with frozen:
        result = runner.run_task(task, timeout_s=args.timeout)
    args.out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    if result.exit_ok:
        return EXIT_OK
    return EXIT_BUDGET if runner.last_error_code == "budget_exceeded" else EXIT_HARNESS


if __name__ == "__main__":
    raise SystemExit(main())
