"""``POST /v1/embeddings`` passthrough for trigger-neighborhood selection.

Embeddings are not a judging tier, so the model id comes from ``embed_model()`` and the
cost row is recorded under the pseudo-tier ``embed`` with purpose ``embed``. That keeps it
out of the nano/super/ultra totals while it still appears in spend by purpose. The same
spending caps as model calls apply (purpose ``embed``), and an upstream error body is
passed back with configured secrets redacted.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from ratchet.config.models import embed_model
from ratchet.config.pricing import embed_cost_usd
from ratchet.errors import UpstreamError
from ratchet.llm.client import HEADER
from ratchet.proxy.headers import BadRequest, valid_run_id
from ratchet.proxy.pipeline import ProxyState
from ratchet.proxy.policy import check_budget
from ratchet.proxy.redact import redact_bytes
from ratchet.store import CostRow
from ratchet.util import new_id

__all__ = ["handle_embeddings"]

_TIMEOUT_S = 30.0


async def handle_embeddings(state: ProxyState, request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError as exc:
        raise BadRequest("body is not JSON") from exc
    if not isinstance(body, dict) or "input" not in body:
        raise BadRequest("embeddings body needs an 'input' field")
    run_id = request.headers.get(HEADER.RUN_ID) or "embed_selection"
    if not valid_run_id(run_id):
        raise BadRequest("invalid run id")
    allowance = state.sidecar.allowance_usd if state.sidecar is not None else None
    await asyncio.to_thread(check_budget, state.store, state.settings, "embed", allowance=allowance)
    model = embed_model(state.settings)
    forwarded = {"model": model, "input": body["input"], "encoding_format": "float"}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = state.upstream.api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if state.upstream.simulator:
        headers[HEADER.PURPOSE] = "embed"
    upstream = await state.upstream.post("embeddings", forwarded, headers, timeout_s=_TIMEOUT_S)
    if upstream.status_code >= 400:
        return Response(
            content=redact_bytes(upstream.content, state.secrets),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )
    try:
        payload = upstream.json()
    except ValueError as exc:
        raise UpstreamError("embedding upstream returned non-JSON") from exc
    usage = payload.get("usage") if isinstance(payload, dict) else None
    tokens = int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else 0
    usd = embed_cost_usd(tokens)
    row = CostRow(
        call_id=new_id("call"),
        run_id=run_id,
        edit_id=request.headers.get(HEADER.EDIT_ID),
        tier="embed",
        model=model,
        prompt_tokens=tokens,
        completion_tokens=0,
        usd=usd,
        purpose="embed",
    )
    await asyncio.to_thread(state.store.record_cost, row)
    return JSONResponse(payload, headers={HEADER.USD: f"{usd:.8f}", HEADER.MODEL: model})
