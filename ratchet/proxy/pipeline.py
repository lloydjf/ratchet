"""One model call through the proxy (the request path).

parse tags → resolve tier → tier policy → rewrite body → response cache (non-agent) →
budget caps → per-purpose semaphore → forward (one retry) → stream or plain → record.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import datetime

import anyio
import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from ratchet import clock
from ratchet.capture.runs import read_run_meta
from ratchet.config.models import spec_for
from ratchet.config.settings import Settings
from ratchet.errors import RatchetError, UpstreamError
from ratchet.llm.client import HEADER
from ratchet.log import bind, get_logger
from ratchet.models import Tier
from ratchet.proxy import cache
from ratchet.proxy.headers import (
    BadRequest,
    CallContext,
    parse_context,
    passthrough_response_headers,
    upstream_headers,
)
from ratchet.proxy.jsonx import as_dict
from ratchet.proxy.metrics import ProxyMetrics
from ratchet.proxy.policy import Limiter, SidecarPolicy, check_budget, check_tier
from ratchet.proxy.recorder import CallRecord, Usage, record_call, scrub, usage_from
from ratchet.proxy.redact import StreamRedactor, redact_bytes, secret_values
from ratchet.proxy.rewrite import (
    control_fields,
    request_hash,
    resolve_tier,
    rewrite_chat,
    rewrite_responses,
)
from ratchet.proxy.stream import ChatStreamAssembler, ResponsesStreamAssembler
from ratchet.proxy.upstream import Upstream
from ratchet.store import Store

__all__ = ["ProxyState", "handle_call", "redact_bytes", "secret_values"]

_log = get_logger("proxy")
_PATHS = {"chat": "chat/completions", "responses": "responses"}


@dataclass
class ProxyState:
    settings: Settings
    store: Store
    upstream: Upstream
    metrics: ProxyMetrics
    limiter: Limiter
    secrets: tuple[str, ...]
    sidecar: SidecarPolicy | None = None


@dataclass(frozen=True)
class _Call:
    ctx: CallContext
    tier: Tier
    model: str
    api: str
    body: dict[str, object]
    request_hash: str
    started_at: datetime
    t0_ms: int
    step_id: str = ""
    cache_key: str | None = None

    def record(
        self, response: dict[str, object], usage: Usage, *, cached: bool = False
    ) -> CallRecord:
        return CallRecord(
            ctx=self.ctx,
            step_id=self.step_id,
            tier=self.tier,
            model=self.model,
            api=self.api,
            request_body=self.body,
            response_body=response,
            request_hash=self.request_hash,
            usage=usage,
            latency_ms=max(clock.monotonic_ms() - self.t0_ms, 0),
            started_at=self.started_at,
            cached=cached,
        )


def _ratchet_headers(call: _Call, rec: CallRecord | None = None) -> dict[str, str]:
    headers = {HEADER.RUN_ID: call.ctx.run_id, HEADER.MODEL: call.model}
    if call.step_id:
        headers[HEADER.STEP_ID] = call.step_id
    headers[HEADER.CACHED] = "1" if rec is not None and rec.cached else "0"
    if rec is not None:
        headers[HEADER.USD] = f"{rec.usd:.8f}"
        headers[HEADER.USAGE_ESTIMATED] = "1" if rec.usage.estimated else "0"
    return headers


def _done(state: ProxyState, rec: CallRecord, status: str) -> None:
    state.metrics.observe(
        tier=rec.tier,
        purpose=rec.ctx.purpose,
        status=status,
        usd=rec.usd,
        latency_ms=rec.latency_ms,
        prompt_tokens=0 if rec.cached else rec.usage.prompt_tokens,
        cached_prompt_tokens=0 if rec.cached else rec.usage.cached_prompt_tokens,
    )
    _log.info(
        "proxy.call.done",
        step_id=rec.step_id,
        tier=rec.tier,
        model=rec.model,
        purpose=rec.ctx.purpose,
        api=rec.api,
        status=status,
        cached=rec.cached,
        prompt_tokens=rec.usage.prompt_tokens,
        completion_tokens=rec.usage.completion_tokens,
        cached_prompt_tokens=rec.usage.cached_prompt_tokens,
        usage_estimated=rec.usage.estimated,
        usd=rec.usd,
        latency_ms=rec.latency_ms,
    )


async def _read_body(request: Request) -> dict[str, object]:
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else None
    except ValueError as exc:
        raise BadRequest("request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise BadRequest("request body must be a JSON object")
    return as_dict(payload)


async def handle_call(
    state: ProxyState, request: Request, *, api: str, path_run_id: str | None = None
) -> Response:
    ctx = parse_context(request.headers, path_run_id)
    if state.sidecar is not None:
        # The caller is the sandboxed harness: its purpose/tier tags are not trusted.
        ctx = replace(ctx, purpose="agent", tier_header=state.sidecar.agent_tier, prompt_name=None)
    body = await _read_body(request)
    tier = resolve_tier(ctx, body.get("model"), state.settings)
    with bind(run_id=ctx.run_id, edit_id=ctx.edit_id, session_hash=ctx.session_hash):
        try:
            return await _dispatch(state, ctx, body, tier, api)
        except RatchetError as exc:
            state.metrics.observe(tier=tier, purpose=ctx.purpose, status=exc.code)
            exc.context.setdefault("run_id", ctx.run_id)
            raise


async def _prepare(
    state: ProxyState, ctx: CallContext, body: dict[str, object], tier: Tier, api: str
) -> _Call:
    meta = await asyncio.to_thread(read_run_meta, state.store, ctx.run_id)
    check_tier(ctx.purpose, tier, meta)
    if api == "chat":
        seed = meta.seed if meta is not None else None
        fwd = rewrite_chat(body, ctx=ctx, tier=tier, seed=seed, settings=state.settings)
    else:
        fwd = rewrite_responses(body, ctx=ctx, tier=tier, settings=state.settings)
    model = str(fwd["model"])
    rhash = request_hash(fwd, api=api)
    key = None
    if cache.cacheable(ctx.purpose, stream=fwd.get("stream") is True):
        key = cache.cache_key(rhash, model, control_fields(fwd))
    return _Call(ctx, tier, model, api, fwd, rhash, clock.now(), clock.monotonic_ms(), "", key)


async def _dispatch(
    state: ProxyState, ctx: CallContext, body: dict[str, object], tier: Tier, api: str
) -> Response:
    call = await _prepare(state, ctx, body, tier, api)
    store = state.store
    if call.cache_key is not None:
        ttl = state.settings.ratchet_cache_ttl_days
        hit = await asyncio.to_thread(cache.lookup, store, call.cache_key, ttl)
        if hit is not None:
            return await _serve_cached(state, call, hit)
    allowance = state.sidecar.allowance_usd if state.sidecar is not None else None
    await asyncio.to_thread(check_budget, store, state.settings, ctx.purpose, allowance=allowance)
    stream = call.body.get("stream") is True
    headers = upstream_headers(
        ctx, api_key=state.upstream.api_key(), simulator=state.upstream.simulator, stream=stream
    )
    step_id = await asyncio.to_thread(store.next_step_id, ctx.run_id, "m")
    call = replace(call, step_id=step_id)
    timeout_s = spec_for(tier, state.settings).timeout_s
    if stream:
        return await _forward_stream(state, call, headers, timeout_s)
    async with state.limiter.semaphore(ctx.purpose):
        response = await state.upstream.post(_PATHS[api], call.body, headers, timeout_s=timeout_s)
    return await _finish_plain(state, call, response)


async def _serve_cached(state: ProxyState, call: _Call, hit: dict[str, object]) -> Response:
    step_id = await asyncio.to_thread(state.store.next_step_id, call.ctx.run_id, "m")
    call = replace(call, step_id=step_id)
    rec = call.record(hit, usage_from(hit, call.body, call.api), cached=True)
    await asyncio.to_thread(record_call, state.store, rec, state.secrets)
    _done(state, rec, "cached")
    return Response(
        content=redact_bytes(json.dumps(hit, ensure_ascii=False).encode("utf-8"), state.secrets),
        media_type="application/json",
        headers=_ratchet_headers(call, rec),
    )


def _passthrough_error(
    state: ProxyState, call: _Call, upstream: httpx.Response, raw: bytes
) -> Response:
    """Upstream 4xx goes back to the harness so it sees its own mistakes, with one
    exception: configured secret values are redacted, because an upstream that echoes the
    key would otherwise hand it to the sandboxed harness."""
    state.metrics.observe(tier=call.tier, purpose=call.ctx.purpose, status="client_error")
    _log.info(
        "proxy.call.rejected",
        step_id=call.step_id,
        tier=call.tier,
        purpose=call.ctx.purpose,
        status=upstream.status_code,
    )
    headers = {**passthrough_response_headers(upstream.headers), **_ratchet_headers(call)}
    return Response(
        content=redact_bytes(raw, state.secrets),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        headers=headers,
    )


def _scrubbed(payload: dict[str, object], state: ProxyState) -> dict[str, object]:
    """What goes into the response cache: no secret values, no auth-looking keys."""
    clean = scrub(payload, state.secrets)
    return clean if isinstance(clean, dict) else {}


async def _finish_plain(state: ProxyState, call: _Call, upstream: httpx.Response) -> Response:
    raw = upstream.content
    if upstream.status_code >= 400:
        return _passthrough_error(state, call, upstream, raw)
    try:
        payload = as_dict(json.loads(raw))
    except ValueError as exc:
        raise UpstreamError("upstream returned a body that is not JSON") from exc
    rec = call.record(payload, usage_from(payload, call.body, call.api))
    await asyncio.to_thread(record_call, state.store, rec, state.secrets)
    if call.cache_key is not None and payload:
        await asyncio.to_thread(
            cache.store_response, state.store, call.cache_key, call.model, _scrubbed(payload, state)
        )
    _done(state, rec, "ok")
    headers = {**passthrough_response_headers(upstream.headers), **_ratchet_headers(call, rec)}
    return Response(
        content=redact_bytes(raw, state.secrets),
        status_code=upstream.status_code,
        media_type="application/json",
        headers=headers,
    )


async def _forward_stream(
    state: ProxyState, call: _Call, headers: dict[str, str], timeout_s: float
) -> Response:
    sem = state.limiter.semaphore(call.ctx.purpose)
    await sem.acquire()
    try:
        upstream = await state.upstream.post(
            _PATHS[call.api], call.body, headers, timeout_s=timeout_s, stream=True
        )
    except BaseException:
        sem.release()
        raise
    if upstream.status_code >= 400:
        try:
            raw = await upstream.aread()
        finally:
            await upstream.aclose()
            sem.release()
        return _passthrough_error(state, call, upstream, raw)
    assembler: ChatStreamAssembler | ResponsesStreamAssembler = (
        ChatStreamAssembler() if call.api == "chat" else ResponsesStreamAssembler()
    )

    redactor = StreamRedactor(state.secrets)

    async def relay() -> AsyncIterator[bytes]:
        completed = False
        try:
            async for chunk in upstream.aiter_bytes():
                assembler.feed(chunk)
                out = redactor.feed(chunk)
                if out:
                    yield out
            tail = redactor.flush()
            if tail:
                yield tail
            completed = True
        finally:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
                sem.release()
                await _record_stream(state, call, assembler, completed)

    out_headers = {**passthrough_response_headers(upstream.headers), **_ratchet_headers(call)}
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
        headers=out_headers,
    )


async def _record_stream(
    state: ProxyState,
    call: _Call,
    assembler: ChatStreamAssembler | ResponsesStreamAssembler,
    completed: bool,
) -> None:
    """Record the reassembled stream; never raises (the bytes are already with the caller)."""
    try:
        assembler.close()
        payload = assembler.response()
        rec = call.record(payload, usage_from(payload, call.body, call.api))
        await asyncio.to_thread(record_call, state.store, rec, state.secrets)
        _done(state, rec, "ok" if completed else "stream_broken")
    except (OSError, ValueError, RatchetError) as exc:
        _log.error("proxy.stream.record_failed", step_id=call.step_id, error=type(exc).__name__)
