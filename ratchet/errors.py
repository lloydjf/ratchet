"""One exception hierarchy.

Every error carries a stable ``code``, whether it is ``retryable``, and a
``context`` dict of ids — never message content or secrets.
"""

from __future__ import annotations

__all__ = [
    "RatchetError",
    "ConfigError",
    "UpstreamError",
    "SandboxError",
    "HarnessError",
    "GitError",
    "SchemaError",
    "BudgetExceeded",
    "NotFoundError",
    "ConflictError",
    "ForbiddenError",
]


class RatchetError(Exception):
    code: str = "ratchet_error"
    retryable: bool = False
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.context: dict[str, object] = dict(context or {})

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ConfigError(RatchetError):
    code = "config_error"
    http_status = 500


class UpstreamError(RatchetError):
    """Token Factory, Tavily, or the Nebius API failed."""

    code = "upstream_unavailable"
    retryable = True
    http_status = 502


class SandboxError(RatchetError):
    code = "sandbox_failed"
    http_status = 500


class HarnessError(RatchetError):
    code = "harness_failed"
    http_status = 500


class GitError(RatchetError):
    code = "git_error"
    http_status = 500


class SchemaError(RatchetError):
    code = "schema_failure"
    http_status = 422


class BudgetExceeded(RatchetError):
    code = "budget_exceeded"
    http_status = 429


class NotFoundError(RatchetError):
    code = "not_found"
    http_status = 404


class ConflictError(RatchetError):
    """A state conflict: tier_mismatch, action_locked, merge_conflict, harness_mismatch."""

    code = "conflict"
    http_status = 409


class ForbiddenError(RatchetError):
    code = "not_public"
    http_status = 403
