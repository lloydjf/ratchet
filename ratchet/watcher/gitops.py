"""The skills repository: ``main`` (what the harness reads) and ``candidate`` (ADR 3).

Layout: ``<main_dir>`` is a checkout of ``main``; ``<main_dir>-candidate`` is a worktree of
``candidate``. ``main`` only moves through :meth:`SkillsRepo.merge_candidate` and
:meth:`SkillsRepo.revert`, always by fast-forward — history is never rewritten.
Harness self-edits land in the ``main`` checkout and are moved onto ``candidate`` by
:meth:`SkillsRepo.capture_main_changes`. Every mutation takes a per-repository lock that
the watcher shares, so a commit never interleaves with a capture.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ratchet.errors import ConflictError, GitError
from ratchet.log import get_logger
from ratchet.store.archive import pack_dir
from ratchet.watcher._git import run_git, run_git_bytes
from ratchet.watcher.repo_base import (
    AUTHORS,
    DEFAULT_IGNORES,
    AuthorKind,
    RepoBase,
    author_arg,
    author_kind,
    split_z,
)

__all__ = [
    "AuthorKind",
    "RevertResult",
    "AUTHORS",
    "DEFAULT_IGNORES",
    "SkillsRepo",
    "author_arg",
    "author_kind",
]

_log = get_logger("watcher")


@dataclass(frozen=True)
class RevertResult:
    """The revert commit on candidate and whether ``main`` fast-forwarded to it."""

    commit: str
    main_advanced: bool


class SkillsRepo(RepoBase):
    """``SkillsRepo(main_dir)``; candidate worktree at ``<main_dir>-candidate``."""

    # -- actions ---------------------------------------------------------------------
    def _guard_main_checkout(self, target: str) -> None:
        branch = run_git(self.main_dir, "symbolic-ref", "--short", "-q", "HEAD", check=False)
        if branch.out.strip() != "main":
            raise GitError("the main checkout is not on branch main", code="main_integrity")
        overlap = set(self.changed_files(self.main_head(), target)) & set(self.dirty_paths("main"))
        if overlap:
            raise ConflictError(
                "uncommitted harness writes conflict with the merge",
                code="merge_conflict",
                context={"n_files": len(overlap)},
            )

    def _fast_forward_main(self, target: str) -> None:
        before = self.main_head()
        self._guard_main_checkout(target)
        result = run_git(self.main_dir, "merge", "--ff-only", "-q", target, check=False)
        if not result.ok:
            raise ConflictError("main cannot fast-forward to candidate", code="merge_conflict")
        self._check_main_integrity(before, target)

    def _check_main_integrity(self, before: str, target: str) -> None:
        """Main points at the target and its checkout matches the merged tree."""
        head = run_git(self.main_dir, "rev-parse", "HEAD").out.strip()
        files = self.changed_files(before, target)
        clean = (
            not files
            or run_git(self.main_dir, "diff", "--quiet", "HEAD", "--", *files, check=False).ok
        )
        if self.main_head() != target or head != target or not clean:
            _log.error("git.main_integrity_failed", commit=target)
            raise GitError(
                "main failed its integrity check after the action", code="main_integrity"
            )

    def merge_candidate(self, verdict_id: str, *, commit: str | None = None) -> str:
        """Fast-forward ``main`` to ``candidate`` (or to ``commit`` on it); returns main's head.

        When merging the candidate head, an empty marker commit ``ratchet: merge verdict
        <id>`` is appended first so the merge is recorded in history while staying
        fast-forward. Divergence or conflicting writes raise ``ConflictError(merge_conflict)``.
        """
        with self.lock:
            main, cand = self.main_head(), self.candidate_head()
            target = self.resolve(commit) if commit else cand
            if target == main:
                return main
            if not self.is_ancestor(main, target):
                raise ConflictError("main has diverged from candidate", code="merge_conflict")
            if not self.is_ancestor(target, cand):
                raise GitError("commit is not on the candidate branch", code="git_error")
            self._guard_main_checkout(target)
            message = f"ratchet: merge verdict {verdict_id}"
            if target == cand:
                tree = self.head_tree_hash(target)
                target = run_git(
                    self.main_dir, "commit-tree", tree, "-p", target, "-m", message
                ).out.strip()
                run_git(
                    self.main_dir, "update-ref", "-m", message, "refs/heads/candidate", target, cand
                )
            else:
                run_git(self.main_dir, "notes", "--ref=ratchet", "add", "-f", "-m", message, target)
            self._fast_forward_main(target)
            _log.info("git.merged", verdict_id=verdict_id, commit=target)
            return target

    def revert(self, commit: str) -> str:
        """``git revert --no-edit`` on candidate; returns the revert commit (see
        :meth:`revert_commit` for whether ``main`` advanced)."""
        return self.revert_commit(commit).commit

    def revert_commit(self, commit: str, *, require_main: bool = False) -> RevertResult:
        """``git revert --no-edit`` on candidate, then fast-forward main to the revert when
        only the reverted commit (and its revert) separate them.

        ``require_main`` (Rollback of a merged edit): main must end up
        without the change, so when main could not fast-forward to the revert — other
        unjudged commits sit between them — nothing is done and
        ``ConflictError(stacked_on_held)`` is raised before candidate is touched.
        """
        with self.lock:
            sha = self.resolve(commit)
            main = self.main_head()
            if require_main and not self._revert_can_reach_main(sha, main):
                raise ConflictError(
                    "main cannot take the revert without later unjudged edits",
                    code="stacked_on_held",
                )
            reverted = self._revert_on_candidate(sha)
            pending = split_z(run_git(self.main_dir, "rev-list", "-z", f"{main}..{reverted}").out)
            advanced = False
            if set(pending) <= {sha, reverted} and self.is_ancestor(main, reverted):
                self._fast_forward_main(reverted)
                advanced = True
            else:
                _log.warning(
                    "git.revert_main_not_advanced", commit=reverted, n_pending=len(pending)
                )
            _log.info("git.reverted", commit=reverted, main_advanced=advanced)
            return RevertResult(commit=reverted, main_advanced=advanced)

    def reapply(self, revert_commit: str) -> str:
        """Revert a revert on candidate (Approve on a blocked edit re-applies its change);
        main is not moved here — :meth:`merge_candidate` does that. Returns the new commit."""
        with self.lock:
            commit = self._revert_on_candidate(self.resolve(revert_commit))
            _log.info("git.reapplied", commit=commit)
            return commit

    def _revert_on_candidate(self, sha: str) -> str:
        result = run_git(self.candidate_dir, "revert", "--no-edit", sha, check=False)
        if not result.ok:
            in_progress = run_git(
                self.candidate_dir, "rev-parse", "-q", "--verify", "REVERT_HEAD", check=False
            ).ok
            run_git(self.candidate_dir, "revert", "--abort", check=False)
            if in_progress or "overwritten" in result.err:
                raise ConflictError("revert conflicts with later edits", code="merge_conflict")
            raise GitError("git revert failed", code="git_error", context={"commit": sha})
        return self.candidate_head()

    def _revert_can_reach_main(self, sha: str, main: str) -> bool:
        cand = self.candidate_head()
        pending = set(split_z(run_git(self.main_dir, "rev-list", "-z", f"{main}..{cand}").out))
        return self.is_ancestor(main, cand) and pending <= {sha}

    def apply_files(
        self, files: dict[str, str | None], *, message: str, author: str = "seeded"
    ) -> str:
        """Write (``str``) or delete (``None``) files in the candidate worktree and commit
        exactly those paths (Simulate's seeded edits). Returns the candidate head."""
        with self.lock:
            root = self.candidate_dir.resolve()
            touched: list[str] = []
            for rel, content in sorted(files.items()):
                path = (root / rel).resolve()
                if not path.is_relative_to(root) or ".git" in Path(rel).parts:
                    raise GitError("seeded file path escapes the skills repo", code="git_error")
                tracked = bool(run_git(root, "ls-files", "-z", "--", rel).out)
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                if content is not None or tracked:
                    touched.append(rel)
            if touched:
                run_git(root, "add", "-A", "--", *touched)
            staged = run_git(root, "diff", "--cached", "--quiet", "--", *touched, check=False)
            if not touched or staged.ok:
                return self.candidate_head()
            args = ["commit", "-q", "--no-verify", "--no-gpg-sign", "-m", message]
            run_git(root, *args, "--author", author_arg(author), "--", *touched)
            return self.candidate_head()

    def reset_candidate(self) -> None:
        """``candidate := main`` and a clean candidate worktree (Simulate auto-revert)."""
        with self.lock:
            run_git(self.candidate_dir, "reset", "-q", "--hard", "refs/heads/main")
            run_git(self.candidate_dir, "clean", "-fdq")

    # -- copies -----------------------------------------------------------------------
    def clone_to(self, dest: Path) -> SkillsRepo:
        """An independent repository (own ``main``, ``candidate`` and worktree) at ``dest``."""
        dest = Path(dest).absolute()
        if dest.exists() and any(dest.iterdir()):
            raise GitError("clone destination is not empty", code="git_error")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            args = (
                "clone",
                "-q",
                "--no-hardlinks",
                "--branch",
                "main",
                str(self.main_dir),
                str(dest),
            )
            run_git(dest.parent, *args)
        run_git(dest, "branch", "candidate", "refs/remotes/origin/candidate")
        run_git(dest, "remote", "remove", "origin")
        clone = SkillsRepo(dest)
        clone.init()
        return clone

    def checkout_tar(self, ref: str, dest_tar: Path) -> Path:
        """Deterministic ``.tar.zst`` of the tree at ``ref`` (replay batch ``skills.tar.zst``)."""
        raw = run_git_bytes(self.main_dir, "archive", "--format=tar", self.resolve(ref))
        with tempfile.TemporaryDirectory(prefix="ratchet-skills-") as tmp:
            root = Path(tmp).resolve()
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
                for member in tar.getmembers():
                    target = (root / member.name).resolve()
                    if not member.isfile() or not target.is_relative_to(root):
                        continue
                    extracted = tar.extractfile(member)
                    if extracted is not None:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(extracted.read())
            return pack_dir(root, dest_tar)
