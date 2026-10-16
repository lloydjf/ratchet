"""Read-only information tools: web search/fetch, git log, and the clock."""

from __future__ import annotations

from ratchet.mocktools.adapters.base import Adapter, Args, Result, not_found, opt_str, req_str
from ratchet.mocktools.world import CallContext, WorldState
from ratchet.mocktools.world_models import Commit

__all__ = ["ADAPTERS", "norm_query"]


def norm_query(query: str) -> str:
    return " ".join(query.casefold().split())


def web_search(state: WorldState, args: Args, ctx: CallContext) -> Result:
    query = req_str(args, "query")
    key = norm_query(query)
    canned = {norm_query(q): results for q, results in state.web.search.items()}
    if key in canned:
        results = [r.model_dump(mode="json") for r in canned[key]]
    else:
        # No canned answer: every page whose title or text holds all query words, by URL.
        words = key.split()
        results = [
            {"title": page.title, "url": url, "snippet": page.text[:200]}
            for url, page in sorted(state.web.pages.items())
            if all(w in f"{page.title}\n{page.text}".casefold() for w in words)
        ]
    return {"query": query, "results": results}


def web_fetch(state: WorldState, args: Args, ctx: CallContext) -> Result:
    url = req_str(args, "url").strip()
    page = state.web.pages.get(url) or state.web.pages.get(url.rstrip("/"))
    if page is None:
        page = state.web.pages.get(url + "/")
    if page is None:
        raise not_found("page", url)
    return {"url": url, **page.model_dump(mode="json")}


def _since(commits: list[Commit], since: str | None) -> list[Commit]:
    if not since:
        return commits
    for index, commit in enumerate(commits):
        if since in commit.tags:
            return commits[:index]
    return [c for c in commits if c.date > since]


def git_log(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name = req_str(args, "repo").strip()
    commits = state.git.repos.get(name)
    if commits is None:
        # `acme/widgets` and a checkout path `./widgets` both name the repo `widgets`.
        commits = state.git.repos.get(name.rstrip("/").rsplit("/", 1)[-1])
    if commits is None:
        raise not_found("git repository", name)
    selected = _since(commits, opt_str(args, "since"))
    return {"repo": name, "commits": [c.model_dump(mode="json") for c in selected]}


def time_now(state: WorldState, args: Args, ctx: CallContext) -> Result:
    return {
        "now": ctx.now_iso,
        "date": ctx.now.date().isoformat(),
        "weekday": ctx.now.strftime("%A"),
        "timezone": "UTC",
        "epoch_s": int(ctx.now.timestamp()),
    }


ADAPTERS: dict[str, Adapter] = {
    "web.search": web_search,
    "web.fetch": web_fetch,
    "git.log": git_log,
    "time.now": time_now,
}
