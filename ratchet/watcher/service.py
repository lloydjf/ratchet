"""The skills watcher: file events → one debounced candidate commit → SkillEdit.

Two watchdog observers watch the ``main`` checkout (where the harness writes) and the
candidate worktree (where a person edits). After ``debounce_ms`` without events, one flush
moves harness writes off ``main`` (:meth:`SkillsRepo.capture_main_changes`), commits
everything pending on ``candidate`` as ONE commit and calls ``on_edit(edit, diff_text)``.
A periodic poll (default every 5 s) catches anything the observers missed; tests drive
:meth:`Watcher.poll_once` directly.

Author classification (step 4) is pluggable. Hermes edit-log and process-ancestry
attribution are still unverified, so the default resolver uses where the write landed:
the main checkout → ``agent``, the candidate worktree → ``user``. Seeded edits never pass
through here (``apply_files`` commits them directly; see ``ratchet.watcher.edits``).
"""

from __future__ import annotations

import fnmatch
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from ratchet import clock
from ratchet.errors import RatchetError
from ratchet.log import get_logger
from ratchet.models import SkillEdit
from ratchet.watcher.edits import build_edit
from ratchet.watcher.gitops import AuthorKind, SkillsRepo

__all__ = ["ChangeSet", "AuthorResolver", "default_author", "Watcher"]

Source = Literal["main", "candidate"]
_IGNORED_EVENTS = frozenset({"opened", "closed_no_write"})
_log = get_logger("watcher")


@dataclass(frozen=True)
class ChangeSet:
    """What one flush found: harness writes in ``main`` and direct candidate writes."""

    main_paths: tuple[str, ...]
    candidate_paths: tuple[str, ...]


AuthorResolver = Callable[[ChangeSet], AuthorKind]


def default_author(changes: ChangeSet) -> AuthorKind:
    return "agent" if changes.main_paths else "user"


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: Watcher, source: Source, root: Path) -> None:
        self._watcher = watcher
        self._source: Source = source
        self._root = root

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in _IGNORED_EVENTS or (
            event.is_directory and event.event_type == "modified"
        ):
            return
        paths = [event.src_path, event.dest_path]
        for raw in paths:
            path = raw.decode() if isinstance(raw, bytes) else raw
            if path and not self._watcher.is_ignored(self._root, Path(path)):
                self._watcher.touch(self._source)
                return


class Watcher:
    def __init__(
        self,
        repo: SkillsRepo,
        on_edit: Callable[[SkillEdit, str], None],
        *,
        debounce_ms: int = 750,
        poll_interval_s: float = 5.0,
        author_resolver: AuthorResolver | None = None,
        session_hash: str | None = None,
        observer_factory: Callable[[], BaseObserver] | None = None,
    ) -> None:
        self.repo = repo
        self.on_edit = on_edit
        self.debounce_s = debounce_ms / 1000.0
        self.poll_interval_s = poll_interval_s
        self.author_resolver = author_resolver or default_author
        self.session_hash = session_hash
        self._observer_factory = observer_factory or Observer
        self._cond = threading.Condition()
        self._flush_lock = threading.Lock()
        self._last_event: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observers: list[BaseObserver] = []
        self._patterns: list[str] = repo.ignore_patterns()

    # -- event intake -----------------------------------------------------------------
    def is_ignored(self, root: Path, path: Path) -> bool:
        """True for ``.git/``, ``*.swp``, ``*~`` and ``.ratchetignore`` globs."""
        try:
            rel = path.relative_to(root)
        except ValueError:
            return True
        if not rel.parts or ".git" in rel.parts:
            return True
        posix = rel.as_posix()
        return any(
            fnmatch.fnmatch(rel.name, pat) or fnmatch.fnmatch(posix, pat) for pat in self._patterns
        )

    def touch(self, source: Source) -> None:
        """Record an event and restart the quiet window."""
        with self._cond:
            self._last_event = time.monotonic()
            self._cond.notify_all()

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        roots: tuple[tuple[Source, Path], ...] = (
            ("main", self.repo.main_dir),
            ("candidate", self.repo.candidate_dir),
        )
        for source, root in roots:
            observer = self._observer_factory()
            observer.schedule(_Handler(self, source, root.resolve()), str(root), recursive=True)
            observer.start()
            self._observers.append(observer)
        self._thread = threading.Thread(target=self._loop, name="ratchet-watcher", daemon=True)
        self._thread.start()
        _log.info("watcher.started", debounce_ms=int(self.debounce_s * 1000))

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        for observer in self._observers:
            observer.stop()
        for observer in self._observers:
            observer.join(timeout_s)
        self._observers.clear()
        if self._thread is not None:
            self._thread.join(timeout_s)
            self._thread = None
        _log.info("watcher.stopped")

    def _next_wait(self, next_poll: float) -> float:
        """Seconds to keep waiting, or 0 when a flush is due. Caller holds ``_cond``."""
        now = time.monotonic()
        if self._last_event is not None:
            remaining = self._last_event + self.debounce_s - now
            if remaining <= 0:
                self._last_event = None
                return 0.0
            return remaining
        return max(next_poll - now, 0.0)

    def _loop(self) -> None:
        next_poll = time.monotonic() + self.poll_interval_s
        while not self._stop.is_set():
            with self._cond:
                wait = self._next_wait(next_poll)
                if wait > 0:
                    self._cond.wait(wait)
                    continue
            if self._stop.is_set():
                break
            next_poll = time.monotonic() + self.poll_interval_s
            self._safe_flush()

    def _safe_flush(self) -> None:
        try:
            self.poll_once()
        except RatchetError as exc:
            _log.error("watcher.flush_failed", code=exc.code)
        except Exception as exc:  # noqa: BLE001 - the watcher thread must survive
            _log.error("watcher.flush_failed", code="internal", error_type=type(exc).__name__)

    # -- flushing ---------------------------------------------------------------------
    def poll_once(self) -> SkillEdit | None:
        """Commit everything pending now; returns the emitted edit (or ``None``)."""
        with self._flush_lock:
            found = self._commit_pending()
        if found is None:
            return None
        edit, diff_text = found
        _log.info(
            "watcher.edit_detected",
            edit_id=edit.edit_id,
            commit=edit.commit,
            author=edit.author,
            n_files=len(edit.files),
            n_skills=len(edit.affected_skills),
        )
        try:
            self.on_edit(edit, diff_text)
        except Exception as exc:  # noqa: BLE001 - a failing consumer must not stop watching
            _log.error(
                "watcher.on_edit_failed", edit_id=edit.edit_id, error_type=type(exc).__name__
            )
        return edit

    def _commit_pending(self) -> tuple[SkillEdit, str] | None:
        repo = self.repo
        with repo.lock:
            repo.refresh_excludes()
            self._patterns = repo.ignore_patterns()
            candidate_paths = repo.dirty_paths("candidate")
            main_paths = repo.capture_main_changes(commit=False)
            if not candidate_paths and not main_paths:
                return None
            changes = ChangeSet(
                tuple(main_paths), tuple(p for p in candidate_paths if p not in set(main_paths))
            )
            author = self.author_resolver(changes)
            parent = repo.main_head()
            message = f"ratchet: skill edit {clock.iso(clock.now())}"
            commit = repo.commit_candidate(message, author=author)
            if commit is None:
                return None
            return build_edit(
                repo, commit=commit, parent=parent, author=author, session_hash=self.session_hash
            )
