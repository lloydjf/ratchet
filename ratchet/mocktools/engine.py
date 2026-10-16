"""The mock tool server's core: record against the world, replay from cassettes.

Synchronous and thread-safe; the FastAPI and MCP front ends call it through
``asyncio.to_thread``. Every served call becomes a ``ToolCall`` step of its run
(``Store.append_step``); replay misses with a side effect other than read are also kept in
the unexpected-side-effect log (``unexpected_side_effects`` = cassette misses with
``side_effect != read``), in memory and in ``traces/<run_id>/unexpected.jsonl``.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.errors import ConfigError, ConflictError, ForbiddenError, NotFoundError, RatchetError
from ratchet.log import get_logger
from ratchet.mocktools.adapters import get_adapter
from ratchet.mocktools.cassettes import CassetteBook, Lookup
from ratchet.mocktools.matching import tolerance_for
from ratchet.mocktools.metrics import CassetteMetrics
from ratchet.mocktools.normalize import (
    Tokenizer,
    args_raw_hash,
    cassette_key,
    normalize_args,
    tokenize_deep,
)
from ratchet.mocktools.synthetic import synthetic_result
from ratchet.mocktools.unexpected import UnexpectedLog
from ratchet.mocktools.world import CallContext, World
from ratchet.models import HARD_SIDE_EFFECTS, SideEffectClass, ToolCall
from ratchet.store import Store
from ratchet.tools.catalog import ToolSpec, get_tool
from ratchet.util import hash_json, preview

__all__ = [
    "Mode",
    "ToolResponse",
    "MockToolEngine",
    "BadRequest",
    "UNKNOWN_TOOL_SIDE_EFFECT",
]

Mode = Literal["record", "replay"]
# A tool missing from the catalog is assumed to be the worst kind of write.
UNKNOWN_TOOL_SIDE_EFFECT: SideEffectClass = "write_irreversible"
_READ: frozenset[str] = frozenset({"none", "read"})
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_log = get_logger("mocktools")


class BadRequest(RatchetError):
    code = "bad_request"
    http_status = 400


@dataclass(frozen=True)
class ToolResponse:
    status: int
    body: dict[str, object]


def _served(response: Mapping[str, object]) -> ToolResponse:
    """Turn a stored cassette response back into the HTTP answer the agent saw."""
    if "error" in response and "result" not in response:
        status = response.get("status")
        code = status if isinstance(status, int) and 400 <= status < 600 else 400
        error = response["error"]
        return ToolResponse(code, {"error": dict(error) if isinstance(error, Mapping) else {}})
    result = response["result"] if "result" in response else dict(response)
    return ToolResponse(200, {"ok": True, "result": result})


class MockToolEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        store: Store,
        mode: Mode,
        cassette_root: Path,
        overlay_root: Path | None = None,
        world: World | None = None,
        tokenizer: Tokenizer | None = None,
        frozen_time: datetime | None = None,
    ) -> None:
        if mode == "record" and world is None:
            raise ConfigError("record mode needs a world directory", code="world_missing")
        self.settings = settings
        self.store = store
        self.world = world
        self.tokenizer = tokenizer
        self.frozen_time = frozen_time
        self.book = CassetteBook(store, cassette_root, overlay_root)
        self.metrics = CassetteMetrics()
        self.log = UnexpectedLog(store)
        self._mode: Mode = mode
        self._lock = threading.Lock()
        self._run_times: dict[str, datetime | None] = {}

    # -- mode ---------------------------------------------------------------------------
    @property
    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> Mode:
        if mode == "record" and self.settings.ratchet_public_mode:
            raise ForbiddenError("record mode is disabled on the public host", code="not_public")
        if mode == "record" and self.world is None:
            raise ConflictError("no world loaded for record mode", code="world_missing")
        self._mode = mode
        _log.info("mocktools.mode", mode=mode)
        return mode

    # -- helpers ------------------------------------------------------------------------
    def _flush_tokens(self) -> None:
        """Persist the PII token map once per call when the hook supports it."""
        flush = getattr(self.tokenizer, "flush", None)
        if callable(flush):
            flush()

    def _tokenize(self, value: object) -> object:
        return tokenize_deep(self.tokenizer, value) if self.tokenizer is not None else value

    def _run_time(self, run_id: str) -> datetime | None:
        with self._lock:
            if run_id in self._run_times:
                return self._run_times[run_id]
        from ratchet.capture.runs import read_run_meta

        meta = read_run_meta(self.store, run_id)
        value = meta.frozen_time if meta is not None else None
        with self._lock:
            self._run_times[run_id] = value
        return value

    def call_time(self, run_id: str, header_time: datetime | None) -> datetime:
        """Header, then the run's ``run.json``, then app config, then the world, then now."""
        for candidate in (header_time, self._run_time(run_id), self.frozen_time):
            if candidate is not None:
                return candidate
        world_time = self.world.frozen_time if self.world is not None else None
        return world_time or clock.now()

    def _resolve(self, tool: str) -> tuple[ToolSpec | None, SideEffectClass]:
        spec = get_tool(tool)
        if spec is not None and spec.local:
            raise BadRequest(
                "local tools run in the sandbox; report them to /tools/{tool}/notify",
                code="local_tool",
            )
        if not _TOOL_NAME.match(tool) or (spec is None and self._mode == "record"):
            raise NotFoundError(f"unknown tool {tool}", code="unknown_tool")
        return spec, spec.side_effect if spec is not None else UNKNOWN_TOOL_SIDE_EFFECT

    def _step(self, run_id: str, call: ToolCall) -> ToolCall:
        self.store.append_step(run_id, call)
        return call

    def _new_call(
        self, run_id: str, tool: str, args_norm: dict[str, object], key: str, **fields: object
    ) -> ToolCall:
        return ToolCall.model_validate(
            {
                "step_id": self.store.next_step_id(run_id, "t"),
                "tool": tool,
                "args_norm": args_norm,
                "args_hash": key,
                "started_at": clock.now(),
                **fields,
            }
        )

    # -- the one entry point ------------------------------------------------------------
    def call(
        self,
        tool: str,
        args: Mapping[str, object],
        *,
        run_id: str,
        frozen_time: datetime | None = None,
    ) -> ToolResponse:
        spec, side_effect = self._resolve(tool)
        args_norm = normalize_args(tool, args, tokenizer=self.tokenizer)
        key = cassette_key(tool, args_norm)
        ctx = CallContext(run_id=run_id, now=self.call_time(run_id, frozen_time))
        try:
            if self._mode == "record":
                return self._record(tool, args, args_norm, key, side_effect, ctx)
            world_dependent = spec.world_dependent if spec is not None else False
            return self._replay(tool, args_norm, key, side_effect, ctx, world_dependent)
        finally:
            self._flush_tokens()

    # -- record -------------------------------------------------------------------------
    def _record(
        self,
        tool: str,
        args: Mapping[str, object],
        args_norm: dict[str, object],
        key: str,
        side_effect: SideEffectClass,
        ctx: CallContext,
    ) -> ToolResponse:
        adapter = get_adapter(tool)
        if adapter is None or self.world is None:
            raise NotFoundError(f"unknown tool {tool}", code="unknown_tool")
        live: ToolResponse
        try:
            result = adapter(self.world.state(ctx.run_id), args, ctx)
            live = ToolResponse(200, {"ok": True, "result": result})
            stored: dict[str, object] = {"result": self._tokenize(result)}
        except RatchetError as exc:
            error = {"code": exc.code, "message": exc.message}
            live = ToolResponse(exc.http_status, {"error": error})
            stored = {"error": self._tokenize(error), "status": exc.http_status}
        action = self.book.record(
            run_id=ctx.run_id,
            tool=tool,
            key=key,
            args_norm=args_norm,
            args_raw_hash=args_raw_hash(args),
            response=stored,
            side_effect=side_effect,
            recorded_at=clock.now(),
            refresh=self.settings.ratchet_cassette_refresh,
        )
        self.metrics.inc("recorded")
        shown = stored.get("result", stored.get("error"))
        self._step(
            ctx.run_id,
            self._new_call(
                ctx.run_id,
                tool,
                args_norm,
                key,
                result_hash=hash_json(stored),
                result_preview=preview(shown),
                side_effect=side_effect,
                source="live",
                cassette_key=key,
            ),
        )
        _log.info("cassette.recorded", tool=tool, args_hash=key, action=action, status=live.status)
        return live

    # -- replay -------------------------------------------------------------------------
    def _replay(
        self,
        tool: str,
        args_norm: dict[str, object],
        key: str,
        side_effect: SideEffectClass,
        ctx: CallContext,
        world_dependent: bool,
    ) -> ToolResponse:
        tolerance = tolerance_for(side_effect)
        found = self.book.lookup(
            tool, args_norm, key, world_dependent=world_dependent, tolerance=tolerance
        )
        if found is not None:
            return self._serve_hit(tool, args_norm, key, side_effect, ctx, found)
        if side_effect in HARD_SIDE_EFFECTS:
            return self._block(tool, args_norm, key, side_effect, ctx)
        result = synthetic_result(tool, args_norm, side_effect, ctx)
        call = self._new_call(
            ctx.run_id,
            tool,
            args_norm,
            key,
            result_hash=hash_json({"result": result}),
            result_preview=preview(result),
            side_effect=side_effect,
            source="cassette",
            cassette_key=None,
        )
        self._step(ctx.run_id, call)
        if side_effect in _READ:
            self.metrics.inc("miss_read")
            _log.info("cassette.miss.read", tool=tool, args_hash=key)
        else:
            self.metrics.inc("miss_write")
            self.log.add(ctx.run_id, call)
            _log.warning("unexpected_side_effect", tool=tool, args_hash=key, blocked=False)
        return ToolResponse(200, {"ok": True, "result": result})

    def _serve_hit(
        self,
        tool: str,
        args_norm: dict[str, object],
        key: str,
        side_effect: SideEffectClass,
        ctx: CallContext,
        found: Lookup,
    ) -> ToolResponse:
        response = self.book.next_response(ctx.run_id, found)
        served = _served(response)
        self.metrics.inc(found.kind)
        self._step(
            ctx.run_id,
            self._new_call(
                ctx.run_id,
                tool,
                args_norm,
                key,
                result_hash=hash_json(response),
                result_preview=preview(served.body.get("result", served.body.get("error"))),
                side_effect=found.cassette.side_effect if get_tool(tool) is None else side_effect,
                source="cassette",
                cassette_key=found.key,
            ),
        )
        event = "cassette.hit" if found.kind == "hit" else "cassette.tolerant_hit"
        _log.info(
            event,
            tool=tool,
            args_hash=key,
            cassette_key=found.key,
            layer=found.layer,
            score=round(found.score, 4),
        )
        return served

    def _block(
        self,
        tool: str,
        args_norm: dict[str, object],
        key: str,
        side_effect: SideEffectClass,
        ctx: CallContext,
    ) -> ToolResponse:
        call = self._new_call(
            ctx.run_id,
            tool,
            args_norm,
            key,
            result_hash=None,
            result_preview="blocked: ratchet_blocked",
            side_effect=side_effect,
            source="cassette",
            cassette_key=None,
            blocked=True,
        )
        self._step(ctx.run_id, call)
        self.log.add(ctx.run_id, call)
        self.metrics.inc("blocked")
        _log.warning(
            "unexpected_side_effect",
            tool=tool,
            args_hash=key,
            side_effect=side_effect,
            blocked=True,
        )
        message = f"{tool} ({side_effect}) is not in this replay's cassettes; blocked by Ratchet"
        return ToolResponse(409, {"error": {"code": "ratchet_blocked", "message": message}})

    # -- unexpected log -----------------------------------------------------------------
    def unexpected(self, run_id: str | None = None) -> dict[str, list[ToolCall]]:
        return self.log.by_run(run_id)

    # -- local tool notifications -------------------------------------------------------
    def notify(
        self, tool: str, args: Mapping[str, object], *, run_id: str, result: object = None
    ) -> ToolCall:
        spec = get_tool(tool)
        if spec is None or not spec.local:
            raise BadRequest(f"{tool} is not a local tool", code="not_local")
        if spec.side_effect not in _READ:
            raise BadRequest(
                f"{tool} is reconciled from the workspace diff; do not notify it",
                code="reconciled_by_diff",
            )
        args_norm = normalize_args(tool, args, tokenizer=self.tokenizer)
        key = cassette_key(tool, args_norm)
        shown = self._tokenize(result) if result is not None else None
        call = self._new_call(
            run_id,
            tool,
            args_norm,
            key,
            result_hash=hash_json(shown) if shown is not None else None,
            result_preview=preview(shown) if shown is not None else "",
            side_effect=spec.side_effect,
            source="local",
            cassette_key=None,
        )
        self._flush_tokens()
        _log.info("mocktools.local.notified", tool=tool, args_hash=key)
        return self._step(run_id, call)
