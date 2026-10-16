"""PII tokenizer (CONTRACTS §3).

Emails and phone numbers become stable tokens (``<EMAIL_1>``, ``<PHONE_1>``): the same value
(exact string, so detokenizing restores it byte-for-byte) always maps to the same token
within one token map, so a golden and its replay tokenize
alike. API keys and other secrets become ``<SECRET>`` and are never stored, not even in the
map. The map is Fernet-encrypted at ``data/pii.map.enc``; the key lives outside the data
dir (``settings.pii_key_path``) and is created with 0600 permissions if missing.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import ConfigError

__all__ = ["PiiTokenizer", "SECRET_TOKEN", "find_secrets", "MAP_FILENAME"]

SECRET_TOKEN = "<SECRET>"
MAP_FILENAME = "pii.map.enc"

_LABELED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd)(\s*[:=]\s*[\"']?)"
    r"([^\s\"',;<>]{8,})"
)
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
)
_LONG_HEX = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])")
_LONG_B64 = re.compile(r"(?<![A-Za-z0-9+_-])[A-Za-z0-9+_-]{40,}={0,2}(?![A-Za-z0-9+_-])")
# Hex digests of exactly these lengths are git commits / sha256 hashes Ratchet itself writes.
_DIGEST_LENGTHS = frozenset({40, 64})

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
_PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"\+\d{1,3}[\s.-]?(?:\(\d{1,4}\)[\s.-]?)?\d{1,4}(?:[\s.-]?\d{2,4}){2,4}"  # +44 20 7946 0958
    r"|\(\d{3}\)\s?\d{3}[\s.-]\d{4}"  # (415) 555-0134
    r"|\d{3}[.-]\d{3}[.-]\d{4}"  # 415-555-0134
    r")(?![\w])"
)


def _is_secret_b64(candidate: str) -> bool:
    core = candidate.rstrip("=")
    return (
        any(c.isdigit() for c in core)
        and any(c.islower() for c in core)
        and any(c.isupper() for c in core)
    )


def find_secrets(text: str) -> str:
    """Replace secrets in ``text`` with ``<SECRET>`` (pure; no map involved)."""
    text = _LABELED_SECRET.sub(lambda m: m.group(1) + m.group(2) + SECRET_TOKEN, text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(SECRET_TOKEN, text)
    text = _LONG_HEX.sub(
        lambda m: m.group(0) if len(m.group(0)) in _DIGEST_LENGTHS else SECRET_TOKEN, text
    )
    return _LONG_B64.sub(lambda m: SECRET_TOKEN if _is_secret_b64(m.group(0)) else m.group(0), text)


def _load_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes().strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


class PiiTokenizer:
    """Tokenize text and JSON values; the token map persists encrypted between runs."""

    def __init__(self, map_path: Path, key_path: Path) -> None:
        self.map_path = map_path
        self.key_path = key_path
        self._lock = threading.Lock()
        self._fernet: Fernet | None = None
        self._forward: dict[str, dict[str, str]] | None = None  # kind -> value -> token
        self._dirty = False

    @classmethod
    def for_settings(cls, settings: Settings | None = None) -> PiiTokenizer:
        settings = settings or get_settings()
        return cls(settings.ratchet_data_dir / MAP_FILENAME, settings.pii_key_path)

    # -- map persistence ----------------------------------------------------------------------
    def _cipher(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(_load_or_create_key(self.key_path))
        return self._fernet

    def _map(self) -> dict[str, dict[str, str]]:
        if self._forward is None:
            self._forward = {"EMAIL": {}, "PHONE": {}}
            if self.map_path.exists():
                try:
                    raw = self._cipher().decrypt(self.map_path.read_bytes())
                except InvalidToken as exc:
                    raise ConfigError(
                        "PII token map cannot be decrypted with the configured key",
                        context={"map": self.map_path.name},
                    ) from exc
                stored = json.loads(raw)
                for kind, entries in stored.items():
                    self._forward[str(kind)] = {str(k): str(v) for k, v in entries.items()}
        return self._forward

    def save(self) -> None:
        """Write the encrypted map if anything new was tokenized."""
        with self._lock:
            if not self._dirty or self._forward is None:
                return
            token = self._cipher().encrypt(json.dumps(self._forward, sort_keys=True).encode())
            self.map_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.map_path.with_name(f".{self.map_path.name}.{os.getpid()}.tmp")
            tmp.write_bytes(token)
            tmp.replace(self.map_path)
            self._dirty = False

    # -- tokenizing ---------------------------------------------------------------------------
    def _token(self, kind: str, value: str) -> str:
        table = self._map().setdefault(kind, {})
        token = table.get(value)
        if token is None:
            token = f"<{kind}_{len(table) + 1}>"
            table[value] = token
            self._dirty = True
        return token

    def tokenize_text(self, text: str) -> str:
        with self._lock:
            text = find_secrets(text)
            text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
            return _PHONE.sub(lambda m: self._token("PHONE", m.group(0)), text)

    def tokenize_obj(self, value: object) -> object:
        """Tokenize every string in a JSON value (dict keys are left alone)."""
        if isinstance(value, str):
            return self.tokenize_text(value)
        if isinstance(value, dict):
            return {k: self.tokenize_obj(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.tokenize_obj(v) for v in value]
        return value

    def detokenize(self, value: object) -> object:
        """Restore emails and phones from tokens; ``<SECRET>`` stays redacted by design."""
        with self._lock:
            reverse = {
                token: original
                for table in self._map().values()
                for original, token in table.items()
            }
        return _restore(value, reverse)


_TOKEN = re.compile(r"<(?:EMAIL|PHONE)_\d+>")


def _restore(value: object, reverse: dict[str, str]) -> object:
    if isinstance(value, str):
        return _TOKEN.sub(lambda m: reverse.get(m.group(0), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _restore(v, reverse) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore(v, reverse) for v in value]
    return value
