"""Secret redaction for bytes the proxy hands back to a caller.

The harness runs in the sandbox, so nothing the proxy relays may contain a configured
secret: plain responses, error passthroughs, cached payloads and streamed chunks are all
scrubbed. A stream is redacted with a carry-over window so a secret split across two
chunks is still caught.
"""

from __future__ import annotations

from pydantic import SecretStr

from ratchet.config.settings import Settings

__all__ = ["SECRET_BYTES", "redact_bytes", "secret_values", "StreamRedactor"]

SECRET_BYTES = b"<SECRET>"
_MIN_SECRET_LEN = 8


def redact_bytes(raw: bytes, secrets: tuple[str, ...]) -> bytes:
    """Replace every configured secret value in ``raw`` with ``<SECRET>``."""
    for secret in secrets:
        if secret:
            raw = raw.replace(secret.encode("utf-8"), SECRET_BYTES)
    return raw


def secret_values(settings: Settings) -> tuple[str, ...]:
    """Every configured secret, so recorded payloads can be scrubbed of them."""
    values: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr) and len(value.get_secret_value()) >= _MIN_SECRET_LEN:
            values.append(value.get_secret_value())
    return tuple(values)


class StreamRedactor:
    """Redact a byte stream chunk by chunk.

    Holds back the last ``max_len - 1`` bytes of each chunk: any secret occurrence that is
    not complete yet must start inside that tail, so emitting everything before it never
    leaks a prefix of a secret that the next chunk would complete.
    """

    def __init__(self, secrets: tuple[str, ...]) -> None:
        self._secrets = tuple(s for s in secrets if s)
        self._keep = max((len(s.encode("utf-8")) for s in self._secrets), default=1) - 1
        self._buffer = b""

    def feed(self, chunk: bytes) -> bytes:
        if not self._secrets:
            return chunk
        self._buffer = redact_bytes(self._buffer + chunk, self._secrets)
        if self._keep <= 0:
            out, self._buffer = self._buffer, b""
            return out
        cut = max(len(self._buffer) - self._keep, 0)
        out, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return out

    def flush(self) -> bytes:
        out, self._buffer = redact_bytes(self._buffer, self._secrets), b""
        return out
