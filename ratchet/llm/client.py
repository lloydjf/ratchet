"""Client for Ratchet's own model calls (judge, bless, bench) — always via the capture proxy.

The proxy is the only caller of Token Factory; it maps the tier to a model id,
records the call, computes cost and returns it in response headers. Nothing here knows a
model string.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import BudgetExceeded, ConflictError, UpstreamError
from ratchet.models import Purpose, Tier

__all__ = ["ChatResult", "ModelClient", "HEADER", "proxy_deadline_s"]

# The proxy retries a failed upstream attempt once after ~2 s ± 25 %
# (``ratchet.proxy.upstream``), and each attempt may take the tier's full timeout. The client
# must wait for both attempts, or a slow first attempt makes the retry pointless.
RETRY_BACKOFF_MAX_S = 2.5
_DEADLINE_SLACK_S = 5.0
_MAX_TIER_TIMEOUT_S = 300.0  # Ultra


def proxy_deadline_s(attempt_timeout_s: float) -> float:
    """How long a caller waits for the proxy: two attempts plus the retry backoff."""
    return 2 * attempt_timeout_s + RETRY_BACKOFF_MAX_S + _DEADLINE_SLACK_S


class HEADER:
    RUN_ID = "X-Ratchet-Run-Id"
    TIER = "X-Ratchet-Tier"
    PURPOSE = "X-Ratchet-Purpose"
    EDIT_ID = "X-Ratchet-Edit-Id"
    SESSION = "X-Ratchet-Session"
    PROMPT = "X-Ratchet-Prompt"  # "<prompt name>@<hash>"; lets the offline simulator dispatch
    THINKING = "X-Ratchet-Thinking"  # "on" | "off"; the proxy maps it to model parameters
    REASONING_BUDGET = "X-Ratchet-Reasoning-Budget"
    USD = "X-Ratchet-Usd"
    STEP_ID = "X-Ratchet-Step-Id"
    CACHED = "X-Ratchet-Cached"
    USAGE_ESTIMATED = "X-Ratchet-Usage-Estimated"
    MODEL = "X-Ratchet-Model"
    ADMIN_TOKEN = "X-Ratchet-Admin-Token"  # replay-worker sidecar admin routes (worker only)


@dataclass(frozen=True)
class ChatResult:
    text: str
    tier: Tier
    model: str
    prompt_tokens: int
    completion_tokens: int
    usd: float
    step_id: str
    cached: bool = False
    reasoning: str | None = None
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    raw: dict[str, object] = field(default_factory=dict)


class ModelClient:
    """One client per logical run (``run_id``), e.g. ``judge_<edit_id>`` or a bless run."""

    def __init__(
        self,
        *,
        run_id: str,
        edit_id: str | None = None,
        session_hash: str | None = None,
        settings: Settings | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.run_id = run_id
        self.edit_id = edit_id
        self.session_hash = session_hash
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> ModelClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            # Ultra's timeout is the longest; per-call timeouts are tighter.
            deadline = proxy_deadline_s(_MAX_TIER_TIMEOUT_S)
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(deadline, connect=10.0))
        return self._http

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
    ) -> ChatResult:
        from ratchet.config.models import spec_for

        body: dict[str, object] = {
            "model": tier,  # replaced by the proxy with the tier's model id
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "top_p": 1,
            "stream": False,
        }
        if seed is not None:
            body["seed"] = seed
        if response_format is not None:
            body["response_format"] = response_format
        if tools:
            body["tools"] = tools
        headers = {HEADER.RUN_ID: self.run_id, HEADER.TIER: tier, HEADER.PURPOSE: purpose}
        if self.edit_id:
            headers[HEADER.EDIT_ID] = self.edit_id
        if self.session_hash:
            headers[HEADER.SESSION] = self.session_hash
        if prompt_name:
            headers[HEADER.PROMPT] = prompt_name
        if thinking is not None:
            headers[HEADER.THINKING] = "on" if thinking else "off"
        if reasoning_budget is not None:
            headers[HEADER.REASONING_BUDGET] = str(reasoning_budget)
        url = self.settings.ratchet_proxy_url.rstrip("/") + "/chat/completions"
        timeout = timeout_s or proxy_deadline_s(spec_for(tier, self.settings).timeout_s)
        try:
            response = await self._client().post(url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                f"{tier} call timed out",
                code="judge_timeout" if tier == "ultra" else "upstream_unavailable",
                context={"tier": tier},
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"proxy unreachable: {type(exc).__name__}", context={"tier": tier}
            ) from exc
        return _parse(response, tier)


def _error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        return str(error.get("code") or "")
    except ValueError:
        return ""


def _parse(response: httpx.Response, tier: Tier) -> ChatResult:
    if response.status_code == 429:
        if _error_code(response) == "budget_exceeded":
            raise BudgetExceeded("spending cap reached", context={"tier": tier})
        # Token Factory rate limiting, passed through unchanged by the proxy.
        raise UpstreamError(
            "model rate limited",
            code="upstream_unavailable",
            context={"tier": tier, "status": 429},
        )
    if response.status_code == 409:
        raise ConflictError("proxy refused the call", code=_error_code(response) or "conflict")
    if response.status_code >= 500:
        raise UpstreamError(
            f"upstream status {response.status_code}",
            code=_error_code(response) or "upstream_unavailable",
            context={"tier": tier, "status": response.status_code},
        )
    if response.status_code >= 400:
        raise UpstreamError(
            f"model rejected the request ({response.status_code})",
            code=_error_code(response) or "upstream_rejected",
            retryable=False,
            context={"tier": tier, "status": response.status_code},
        )
    payload = response.json()
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = payload.get("usage") or {}
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    return ChatResult(
        text=str(message.get("content") or ""),
        tier=tier,
        model=str(response.headers.get(HEADER.MODEL) or payload.get("model") or ""),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        usd=float(response.headers.get(HEADER.USD) or 0.0),
        step_id=str(response.headers.get(HEADER.STEP_ID) or ""),
        cached=response.headers.get(HEADER.CACHED) == "1",
        reasoning=str(reasoning) if reasoning else None,
        tool_calls=list(message.get("tool_calls") or []),
        raw=payload,
    )
