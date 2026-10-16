"""Small pure helpers shared by every component: ids, canonical JSON, hashing, text."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from collections.abc import Mapping
from pathlib import Path

__all__ = [
    "new_id",
    "canonical_json",
    "sha256_hex",
    "hash_json",
    "preview",
    "truncate_middle",
    "copy_tree_contents",
]


def new_id(prefix: str) -> str:
    """Sortable-enough, collision-safe ids: ``run_<12 hex>``, ``edit_<12 hex>``."""
    if not prefix.isidentifier():
        raise ValueError(f"bad id prefix {prefix!r}")
    return f"{prefix}_{secrets.token_hex(6)}"


def canonical_json(value: object) -> str:
    """Sorted keys, no whitespace, UTF-8 safe: the input to every hash in Ratchet."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def hash_json(value: object) -> str:
    return sha256_hex(canonical_json(value))


def preview(value: object, limit: int = 400) -> str:
    """First ``limit`` characters of a value rendered as text (tool result previews)."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, Mapping | list):
        text = canonical_json(value)
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def truncate_middle(text: str, limit: int) -> tuple[str, bool]:
    """Keep the head and tail of ``text`` within ``limit`` chars; report whether it was cut."""
    if len(text) <= limit:
        return text, False
    marker = "\n…[truncated]…\n"
    keep = max(limit - len(marker), 0)
    head = keep * 2 // 3
    tail = keep - head
    return text[:head] + marker + (text[-tail:] if tail else ""), True


def copy_tree_contents(src: Path, dst: Path) -> int:
    """Copy the files under ``src`` into ``dst`` (created as needed) without copying any
    metadata: no chmod, no utime, no xattrs. Object-storage mounts (the Jobs output volume)
    accept writes and renames but refuse ``copystat``, which ``shutil.copytree`` always
    attempts on directories. Symlinks are skipped. Returns the number of files copied."""
    count = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            source = Path(root) / name
            if source.is_symlink():
                continue
            shutil.copyfile(source, dst / rel / name)
            count += 1
    return count
