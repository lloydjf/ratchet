"""``.tar.zst`` helpers for workspace snapshots, skills tarballs and job inputs.

Extraction refuses absolute paths, ``..`` segments, links and devices, so a malicious
workspace or skill tarball cannot write outside its destination.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Iterable
from pathlib import Path

import zstandard

__all__ = ["pack_dir", "unpack", "tree_digest", "list_files"]

_IGNORED_DIRS = frozenset({".git", "__pycache__", ".ratchet"})


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            files.append(path)
    return files


def list_files(root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in _iter_files(root)] if root.exists() else []


def pack_dir(src: Path, dest: Path, *, only: Iterable[str] | None = None) -> Path:
    """Deterministic tarball (sorted, zeroed mtimes/owners) so equal trees hash equal.
    ``only`` restricts it to those relative paths (e.g. a run's changed/new files)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    wanted = None if only is None else frozenset(only)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        if src.exists():
            for path in _iter_files(src):
                if wanted is not None and path.relative_to(src).as_posix() not in wanted:
                    continue
                info = tar.gettarinfo(str(path), arcname=path.relative_to(src).as_posix())
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with path.open("rb") as fh:
                    tar.addfile(info, fh)
    compressed = zstandard.ZstdCompressor(level=6).compress(buffer.getvalue())
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(compressed)
    tmp.replace(dest)
    return dest


def unpack(archive: Path, dest: Path) -> list[str]:
    """Extract into ``dest``; returns the relative paths written."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    raw = zstandard.ZstdDecompressor().decompress(archive.read_bytes(), max_output_size=1 << 31)
    written: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                if member.isdir():
                    continue
                raise ValueError(f"refusing non-file tar member {member.name!r}")
            target = (root / member.name).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"refusing tar member outside destination: {member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            target.write_bytes(extracted.read())
            written.append(member.name)
    return written


def tree_digest(root: Path) -> dict[str, str]:
    """``{relative_path: sha256}`` for every file under ``root`` (workspace diffs)."""
    digest: dict[str, str] = {}
    if not root.exists():
        return digest
    for path in _iter_files(root):
        digest[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest
