"""The offline simulator's FastAPI app (ADR 0018): a deterministic Token Factory stand-in.

Routes: ``POST /v1/chat/completions`` (and the run-scoped ``/v1/r/{run_id}/...`` form the
MinimalRunner uses), plain or SSE streamed; ``POST /v1/embeddings``; ``GET /v1/models``;
``GET /healthz``. Model ids are ``ratchet-sim/<tier>``; the tier comes from the model id
(``ratchet-sim/super`` or a bare ``super``) or the ``X-Ratchet-Tier`` header.

Dispatch: a request with ``X-Ratchet-Prompt`` (or a non-agent ``X-Ratchet-Purpose``) goes to
the judge heuristics (:mod:`ratchet.sim.judge`); everything else to the skill policies
(:mod:`ratchet.sim.agent`). ``usage`` is estimated at ~4 characters per token including tool
schemas, with a stateless prompt-cache hit count (:mod:`ratchet.sim.usage`). Same request →
same bytes.

Everything served here is simulated test infrastructure. It is never Nemotron output, it is
labeled "Simulated models" wherever it surfaces, and no README or video number may use it.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ratchet.config.models import SIM_MODEL_PREFIX
from ratchet.models import TIERS, Tier
from ratchet.sim import agent, judge, usage
from ratchet.sim.embeddings import DEFAULT_DIMENSIONS, embed_all
from ratchet.sim.judge.sections import prompt_name
from ratchet.sim.wire import CREATED, Completion, completion_id, dumps, payload, sse_events

__all__ = ["create_app", "complete", "SIM_HEADER", "resolve_tier"]

SIM_HEADER = "X-Ratchet-Simulated"
_PURPOSE = "x-ratchet-purpose"
_PROMPT = "x-ratchet-prompt"
_TIER = "x-ratchet-tier"


class _BadRequest(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _error(exc: _BadRequest) -> JSONResponse:
    body = {"error": {"message": exc.message, "type": "invalid_request_error", "code": exc.code}}
    return JSONResponse(body, status_code=exc.status, headers={SIM_HEADER: "1"})


def resolve_tier(model: object, header: str | None) -> Tier | None:
    for candidate in (header, model):
        if not isinstance(candidate, str):
            continue
        value = candidate.strip().lower().removeprefix(SIM_MODEL_PREFIX)
        for tier in TIERS:
            if value == tier:
                return tier
    return None


async def _body(request: Request) -> dict[str, object]:
    try:
        body = json.loads(await request.body())
    except ValueError as exc:
        raise _BadRequest(400, "bad_request", "request body is not JSON") from exc
    if not isinstance(body, dict):
        raise _BadRequest(400, "bad_request", "request body must be a JSON object")
    return body


def _thinking(body: dict[str, object]) -> bool:
    kwargs = body.get("chat_template_kwargs")
    if isinstance(kwargs, dict) and kwargs.get("enable_thinking") is True:
        return True
    return body.get("reasoning_effort") in ("low", "medium", "high")


def _reasoning(name: str | None) -> str:
    return (
        f"Simulated reasoning for {name or 'this prompt'}: read each section of the prompt, "
        "compared the golden and replay evidence line by line, and applied the documented "
        "simulator heuristic. No model was run."
    )


def _answer(
    body: dict[str, object], headers: dict[str, str]
) -> tuple[str | None, list[dict[str, object]]]:  # noqa: E501
    raw = body.get("messages")
    if not isinstance(raw, list) or not raw:
        raise _BadRequest(400, "bad_request", "messages must be a non-empty list")
    messages = [m for m in raw if isinstance(m, dict)]
    purpose = (headers.get(_PURPOSE) or "agent").strip().lower()
    prompt = headers.get(_PROMPT)
    if prompt or purpose != "agent":
        return judge.respond(prompt, messages), []
    tools = body.get("tools")
    reply, already = agent.respond(messages, tools if isinstance(tools, list) else None)
    return reply.content, reply.tool_calls(already)


def complete(body: dict[str, object], headers: dict[str, str]) -> Completion:
    """The simulated completion for one request (pure; raises ``_BadRequest``)."""
    tier = resolve_tier(body.get("model"), headers.get(_TIER))
    if tier is None:
        raise _BadRequest(404, "model_not_found", f"unknown model {str(body.get('model'))[:64]!r}")
    content, tool_calls = _answer(body, headers)
    is_judge = bool(headers.get(_PROMPT))
    reasoning = (
        _reasoning(prompt_name(headers.get(_PROMPT))) if is_judge and _thinking(body) else None
    )
    reasoning_tokens = usage.count(reasoning or "")
    answer_tokens = usage.count(content or "") + (
        usage.count(dumps(tool_calls).decode()) if tool_calls else 0
    )
    finish = "tool_calls" if tool_calls else "stop"
    cap = body.get("max_tokens")
    if isinstance(cap, int) and content and not tool_calls and answer_tokens > cap:
        content = content[: cap * usage.CHARS_PER_TOKEN]
        answer_tokens, finish = cap, "length"
    return Completion(
        id=completion_id(body),
        model=f"{SIM_MODEL_PREFIX}{tier}",
        content=content,
        tool_calls=tool_calls,
        reasoning=reasoning,
        finish_reason=finish,
        usage=usage.usage_block(
            usage.prompt_tokens(body),
            answer_tokens + reasoning_tokens,
            reasoning_tokens,
            usage.cached_prompt_tokens(body),
        ),
    )


def _include_usage(body: dict[str, object]) -> bool:
    options = body.get("stream_options")
    return isinstance(options, dict) and options.get("include_usage") is True


async def _chat(request: Request) -> Response:
    try:
        body = await _body(request)
        completion = complete(body, {k.lower(): v for k, v in request.headers.items()})
    except _BadRequest as exc:
        return _error(exc)
    if body.get("stream") is True:
        events = list(sse_events(completion, include_usage=_include_usage(body)))
        return StreamingResponse(
            iter(events), media_type="text/event-stream", headers={SIM_HEADER: "1"}
        )
    return Response(
        dumps(payload(completion)), media_type="application/json", headers={SIM_HEADER: "1"}
    )  # noqa: E501


def _texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [str(v) for v in value]
    raise _BadRequest(400, "bad_request", "input must be a string or a list of strings")


async def _embeddings(request: Request) -> Response:
    try:
        body = await _body(request)
        texts = _texts(body.get("input"))
    except _BadRequest as exc:
        return _error(exc)
    dims = body.get("dimensions")
    size = dims if isinstance(dims, int) and dims > 0 else DEFAULT_DIMENSIONS
    vectors = embed_all(texts, size)
    tokens = sum(usage.count(t) for t in texts)
    result = {
        "object": "list",
        "model": str(body.get("model") or f"{SIM_MODEL_PREFIX}embed"),
        "data": [
            {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }
    return Response(dumps(result), media_type="application/json", headers={SIM_HEADER: "1"})


def _models() -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {
                "id": f"{SIM_MODEL_PREFIX}{tier}",
                "object": "model",
                "created": CREATED,
                "owned_by": "ratchet-sim",
            }
            for tier in TIERS
        ],
    }


def create_app() -> FastAPI:
    """A fresh simulator app; no state is shared between requests or instances."""
    app = FastAPI(title="Ratchet offline simulator (simulated models)", version="1")

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        return await _chat(request)

    @app.post("/v1/r/{run_id}/chat/completions")
    async def chat_for_run(run_id: str, request: Request) -> Response:
        return await _chat(request)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        return await _embeddings(request)

    @app.get("/v1/models")
    async def models() -> Response:
        return Response(dumps(_models()), media_type="application/json")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "simulated": True}

    return app
