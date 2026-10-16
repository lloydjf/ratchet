"""GitHub adapters over the fictional ``github.json`` (demo org ``acme``)."""

from __future__ import annotations

from ratchet.errors import ConflictError
from ratchet.mocktools.adapters.base import (
    Adapter,
    Args,
    Result,
    not_found,
    opt_str,
    req_int,
    req_str,
    str_list,
)
from ratchet.mocktools.world import CallContext, ToolArgumentError, WorldState
from ratchet.mocktools.world_models import Comment, Issue, PullRequest, Repo

__all__ = ["ADAPTERS"]


def _repo(state: WorldState, args: Args) -> tuple[str, Repo]:
    name = req_str(args, "repo").strip()
    repo = state.github.repos.get(name)
    if repo is None:
        raise not_found("repository", name)
    return name, repo


def _issue(repo: Repo, number: int, name: str) -> Issue:
    for issue in repo.issues:
        if issue.number == number:
            return issue
    raise not_found("issue", f"{name}#{number}")


def _pull(repo: Repo, number: int, name: str) -> PullRequest:
    for pull in repo.pulls:
        if pull.number == number:
            return pull
    raise not_found("pull request", f"{name}#{number}")


def _issue_view(issue: Issue) -> Result:
    view = issue.model_dump(mode="json", exclude={"comments"})
    view["comments"] = len(issue.comments)
    return view


def list_issues(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name, repo = _repo(state, args)
    wanted_state = opt_str(args, "state", "open") or "open"
    if wanted_state not in {"open", "closed", "all"}:
        raise ToolArgumentError("state must be open, closed or all")
    labels = set(str_list(args, "labels"))
    issues = [
        _issue_view(issue)
        for issue in sorted(repo.issues, key=lambda i: i.number)
        if (wanted_state == "all" or issue.state == wanted_state) and labels <= set(issue.labels)
    ]
    return {"repo": name, "state": wanted_state, "issues": issues}


def label(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name, repo = _repo(state, args)
    issue = _issue(repo, req_int(args, "issue"), name)
    for item in str_list(args, "labels", required=True):
        if item not in issue.labels:
            issue.labels.append(item)
    issue.updated_at = ctx.now_iso
    return {"repo": name, "issue": issue.number, "labels": list(issue.labels)}


def comment(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name, repo = _repo(state, args)
    issue = _issue(repo, req_int(args, "issue"), name)
    existing = [c.comment_id for i in repo.issues for c in i.comments]
    new = Comment(
        comment_id=max(existing, default=1000) + 1,
        author="ratchet-agent",
        body=req_str(args, "body"),
        created_at=ctx.now_iso,
    )
    issue.comments.append(new)
    issue.updated_at = ctx.now_iso
    return {"repo": name, "issue": issue.number, "comment_id": new.comment_id}


def close_issue(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name, repo = _repo(state, args)
    issue = _issue(repo, req_int(args, "issue"), name)
    reason = opt_str(args, "reason", "completed") or "completed"
    if reason not in {"completed", "not_planned"}:
        raise ToolArgumentError("reason must be completed or not_planned")
    if issue.state != "closed":
        issue.state = "closed"
        issue.state_reason = reason
        issue.closed_at = ctx.now_iso
        issue.updated_at = ctx.now_iso
    return {
        "repo": name,
        "issue": issue.number,
        "state": issue.state,
        "state_reason": issue.state_reason,
    }


def open_pr(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name, repo = _repo(state, args)
    numbers = [i.number for i in repo.issues] + [p.number for p in repo.pulls]
    pull = PullRequest(
        number=max(numbers, default=0) + 1,
        title=req_str(args, "title"),
        body=opt_str(args, "body", "") or "",
        head=req_str(args, "head"),
        base=opt_str(args, "base", "main") or "main",
        author="ratchet-agent",
        created_at=ctx.now_iso,
    )
    repo.pulls.append(pull)
    return {
        "repo": name,
        "pr": pull.number,
        "state": pull.state,
        "url": f"https://github.example.com/{name}/pull/{pull.number}",
    }


def merge_pr(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name, repo = _repo(state, args)
    pull = _pull(repo, req_int(args, "pr"), name)
    if pull.state != "open":
        raise ConflictError(f"pull request is {pull.state}", code="pr_not_open")
    pull.state = "merged"
    pull.merged_at = ctx.now_iso
    return {"repo": name, "pr": pull.number, "merged": True, "merged_at": pull.merged_at}


ADAPTERS: dict[str, Adapter] = {
    "github.list_issues": list_issues,
    "github.label": label,
    "github.comment": comment,
    "github.close_issue": close_issue,
    "github.open_pr": open_pr,
    "github.merge_pr": merge_pr,
}
