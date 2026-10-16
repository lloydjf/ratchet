"""Skills repository plumbing shared by :class:`ratchet.watcher.gitops.SkillsRepo`.

The skills repository: ``main`` (what the harness reads) and ``candidate`` (ADR 3).

Layout: ``<main_dir>`` is a checkout of ``main``; ``<main_dir>-candidate`` is a worktree of
``candidate``. ``main`` only moves through :meth:`SkillsRepo.merge_candidate` and
:meth:`SkillsRepo.revert`, always by fast-forward — history is never rewritten.
Harness self-edits land in the ``main`` checkout and are moved onto ``candidate`` by
:meth:`SkillsRepo.capture_main_changes`. Every mutation takes a per-repository lock that
the watcher shares, so a commit never interleaves with a capture.
"""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path
from typing import Literal

from ratchet.errors import GitError, SchemaError
from ratchet.log import get_logger
from ratchet.skills import SKILL_FILE, parse_skill_md, skill_dirs, skill_for_path
from ratchet.watcher._git import run_git, run_git_bytes

__all__ = [
    "AuthorKind",
    "AUTHORS",
    "DEFAULT_IGNORES",
    "RepoBase",
    "author_arg",
    "author_kind",
    "split_z",
]

AuthorKind = Literal["agent", "user", "seeded"]
AUTHORS: dict[AuthorKind, tuple[str, str]] = {
    "agent": ("Hermes Agent", "agent@ratchet.local"),
    "user": ("Ratchet User", "user@ratchet.local"),
    "seeded": ("Ratchet Seeded", "seeded@ratchet.local"),
}
DEFAULT_IGNORES = ("*.swp", "*.swx", "*~", ".DS_Store")
_IDENTITY_RE = re.compile(r"^[^<>\n]+ <[^<>\s]+>$")
_registry_lock = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_log = get_logger("watcher")


def author_arg(author: str) -> str:
    """``agent|user|seeded`` or an explicit ``Name <email>`` → git ``--author`` value."""
    for kind, (name, email) in AUTHORS.items():
        if author == kind:
            return f"{name} <{email}>"
    if _IDENTITY_RE.match(author):
        return author
    raise GitError("author must be agent, user, seeded or 'Name <email>'", code="git_error")


def author_kind(email: str) -> AuthorKind:
    """Classification carried by a commit's author email; unknown identities are users."""
    for kind, (_, known) in AUTHORS.items():
        if email.strip().lower() == known:
            return kind
    return "user"


def split_z(text: str) -> list[str]:
    return [part for part in text.split("\0") if part]


def _repo_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _registry_lock:
        return _locks.setdefault(key, threading.RLock())


class RepoBase:
    """Setup, refs, working trees, diffs and skill lookups; actions live on SkillsRepo."""

    def __init__(self, main_dir: Path) -> None:
        self.main_dir = Path(main_dir).absolute()
        self.candidate_dir = self.main_dir.with_name(self.main_dir.name + "-candidate")
        self.lock = _repo_lock(self.main_dir)

    # -- setup ------------------------------------------------------------------------
    def init(self, seed: Path | None = None) -> None:
        """Create (or repair) the repo, ``main``/``candidate`` and the candidate worktree.

        Existing files in ``main_dir`` without a repository are adopted as the seed.
        Idempotent on an initialized repository.
        """
        with self.lock:
            if not (self.main_dir / ".git").exists():
                self.main_dir.mkdir(parents=True, exist_ok=True)
                run_git(self.main_dir, "init", "-q", "-b", "main")
                if seed is not None:
                    shutil.copytree(
                        seed,
                        self.main_dir,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git"),
                    )
                self.refresh_excludes()
                run_git(self.main_dir, "add", "-A")
                self._commit(self.main_dir, "ratchet: seed skills", "seeded", allow_empty=True)
            if not self._has_ref("refs/heads/candidate"):
                run_git(self.main_dir, "branch", "candidate", "main")
            self._ensure_worktree()
            self.refresh_excludes()

    def _ensure_worktree(self) -> None:
        if (self.candidate_dir / ".git").exists():
            return
        if self.candidate_dir.exists() and any(self.candidate_dir.iterdir()):
            raise GitError("candidate directory exists and is not a worktree", code="git_error")
        run_git(self.main_dir, "worktree", "prune")
        run_git(self.main_dir, "worktree", "add", "-q", str(self.candidate_dir), "candidate")

    def ignore_patterns(self) -> list[str]:
        """Built-in ignores plus ``.ratchetignore`` globs (one per line, ``#`` comments)."""
        patterns = list(DEFAULT_IGNORES)
        for root in (self.main_dir, self.candidate_dir):
            path = root / ".ratchetignore"
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and line not in patterns:
                        patterns.append(line)
        return patterns

    def refresh_excludes(self) -> None:
        """Mirror the ignore rules into ``info/exclude`` so status and commits honor them."""
        common = run_git(self.main_dir, "rev-parse", "--git-common-dir").out.strip()
        info = (self.main_dir / common).resolve() / "info"
        info.mkdir(parents=True, exist_ok=True)
        text = "# managed by ratchet\n" + "\n".join(self.ignore_patterns()) + "\n"
        exclude = info / "exclude"
        if not exclude.exists() or exclude.read_text(encoding="utf-8") != text:
            exclude.write_text(text, encoding="utf-8")

    # -- refs -------------------------------------------------------------------------
    def _has_ref(self, ref: str) -> bool:
        return run_git(self.main_dir, "rev-parse", "-q", "--verify", ref, check=False).ok

    def resolve(self, ref: str) -> str:
        return run_git(
            self.main_dir, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"
        ).out.strip()

    def main_head(self) -> str:
        return self.resolve("refs/heads/main")

    def candidate_head(self) -> str:
        return self.resolve("refs/heads/candidate")

    def head_tree_hash(self, ref: str = "main") -> str:
        return run_git(self.main_dir, "rev-parse", f"{ref}^{{tree}}").out.strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        args = ("merge-base", "--is-ancestor", ancestor, descendant)
        return run_git(self.main_dir, *args, check=False).status == 0

    def commit_author_kind(self, commit: str) -> AuthorKind:
        return author_kind(run_git(self.main_dir, "log", "-1", "--format=%ae", commit).out)

    # -- working trees ----------------------------------------------------------------
    def dirty_paths(self, which: Literal["main", "candidate"]) -> list[str]:
        root = self.main_dir if which == "main" else self.candidate_dir
        args = ("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
        return sorted(entry[3:] for entry in split_z(run_git(root, *args).out))

    def _commit(self, cwd: Path, message: str, author: str, *, allow_empty: bool = False) -> str:
        args = ["commit", "-q", "--no-verify", "--no-gpg-sign", "-m", message]
        args += ["--author", author_arg(author)] + (["--allow-empty"] if allow_empty else [])
        run_git(cwd, *args)
        return run_git(cwd, "rev-parse", "HEAD").out.strip()

    def commit_candidate(self, message: str, *, author: str = "user") -> str | None:
        """Commit everything pending in the candidate worktree; ``None`` if nothing changed."""
        with self.lock:
            run_git(self.candidate_dir, "add", "-A")
            if run_git(self.candidate_dir, "diff", "--cached", "--quiet", check=False).ok:
                return None
            return self._commit(self.candidate_dir, message, author)

    def capture_main_changes(
        self, *, commit: bool = True, message: str | None = None, author: str = "agent"
    ) -> list[str]:
        """Move uncommitted writes in the ``main`` checkout onto ``candidate``; restore main.

        Files are copied over the candidate worktree (last writer wins), deletions are
        replayed, then the main checkout is reset to its HEAD. With ``commit`` the moved
        files become one candidate commit; the watcher passes ``commit=False`` to fold
        them into the same commit as concurrent candidate-worktree writes.
        """
        with self.lock:
            run_git(self.main_dir, "add", "-A")
            args = ("diff", "--cached", "--name-status", "--no-renames", "-z", "HEAD")
            parts = split_z(run_git(self.main_dir, *args).out)
            changes = list(zip(parts[0::2], parts[1::2], strict=True))
            if not changes:
                return []
            for status, rel in changes:
                self._copy_to_candidate(rel, delete=status.startswith("D"))
            run_git(self.main_dir, "reset", "-q", "--hard", "HEAD")
            paths = sorted(rel for _, rel in changes)
            if commit:
                self.commit_candidate(message or "ratchet: harness skill edit", author=author)
            _log.info("git.main_captured", n_files=len(paths))
            return paths

    def _copy_to_candidate(self, rel: str, *, delete: bool) -> None:
        dst = self.candidate_dir / rel
        if delete:
            if dst.is_file() or dst.is_symlink():
                dst.unlink()
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink():
            dst.unlink()
        shutil.copy2(self.main_dir / rel, dst, follow_symlinks=False)

    # -- diffs ------------------------------------------------------------------------
    def diff(self, base: str, head: str) -> str:
        args = ("diff", "--no-color", "--no-ext-diff", "--no-renames", base, head)
        return run_git(self.main_dir, *args).out

    def commits_between(self, base: str, head: str) -> list[str]:
        """Commits reachable from ``head`` but not ``base`` (``git rev-list base..head``)."""
        out = run_git(self.main_dir, "rev-list", f"{base}..{head}").out
        return [line for line in out.splitlines() if line]

    def changed_files(self, base: str, head: str) -> list[str]:
        args = ("diff", "--name-only", "--no-renames", "-z", base, head)
        return sorted(split_z(run_git(self.main_dir, *args).out))

    def numstat(self, base: str, head: str) -> dict[str, tuple[int, int]]:
        """``{path: (added, removed)}``; binary files count as ``(0, 0)``."""
        out = run_git(self.main_dir, "diff", "--numstat", "--no-renames", "-z", base, head).out
        stats: dict[str, tuple[int, int]] = {}
        for entry in split_z(out):
            added, removed, path = entry.split("\t", 2)
            stats[path] = (
                int(added) if added.isdigit() else 0,
                int(removed) if removed.isdigit() else 0,
            )
        return stats

    def _files_at(self, ref: str) -> list[str]:
        return split_z(run_git(self.main_dir, "ls-tree", "-r", "-z", "--name-only", ref).out)

    def read_file(self, ref: str, path: str) -> bytes | None:
        if run_git(self.main_dir, "cat-file", "-e", f"{ref}:{path}", check=False).status != 0:
            return None
        return run_git_bytes(self.main_dir, "show", f"{ref}:{path}")

    def _frontmatter_names(self, ref: str, dirs: dict[str, str]) -> dict[str, str]:
        names: dict[str, str] = {}
        for skill, rel in dirs.items():
            raw = self.read_file(ref, f"{rel}/{SKILL_FILE}")
            try:
                doc = parse_skill_md(raw.decode("utf-8")) if raw is not None else None
            except (SchemaError, UnicodeDecodeError):
                doc = None
            if doc is not None and doc.name:
                names[doc.name] = skill
        return names

    def affected_skills(self, base: str, head: str) -> list[str]:
        """Skill directories touching changed files, plus skills whose
        frontmatter ``name`` appears in a changed file's path (``shared/<name>.md``)."""
        files = self.changed_files(base, head)
        dirs_base, dirs_head = skill_dirs(self._files_at(base)), skill_dirs(self._files_at(head))
        dirs = {**dirs_base, **dirs_head}
        affected = {skill for f in files if (skill := skill_for_path(f, dirs)) is not None}
        names = {
            **self._frontmatter_names(base, dirs_base),
            **self._frontmatter_names(head, dirs_head),
        }
        for f in files:
            parts = f.split("/")
            tokens = set(parts[:-1]) | {parts[-1], parts[-1].rsplit(".", 1)[0]}
            affected.update(skill for name, skill in names.items() if name in tokens)
        return sorted(affected)

    # -- skills -----------------------------------------------------------------------
    def list_skills(self, ref: str = "main") -> list[str]:
        return sorted(skill_dirs(self._files_at(ref)))

    def skill_description(self, name: str, ref: str = "candidate") -> str:
        """The frontmatter ``description`` of skill ``name`` at ``ref``; ``""`` if absent."""
        rel = skill_dirs(self._files_at(ref)).get(name)
        raw = self.read_file(ref, f"{rel}/{SKILL_FILE}") if rel is not None else None
        if raw is None:
            return ""
        try:
            return parse_skill_md(raw.decode("utf-8")).description
        except (SchemaError, UnicodeDecodeError):
            return ""
