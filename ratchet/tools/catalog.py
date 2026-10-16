"""The tool catalog shared by harnesses, the mock tool server, the simulator and the judge.

Each tool has a dotted name (``github.close_issue``), a default side-effect class (the
rule file ``ratchet/judge/side_effects.yaml`` stays authoritative for classification),
whether it runs locally inside the sandbox (``fs.*``, ``shell.run``)
or is served by the mock tool server, and a JSON-schema parameter block for the
OpenAI function-calling format. OpenAI function names cannot contain dots, so the wire
name replaces ``.`` with ``__`` (:func:`wire_name` / :func:`from_wire_name`). The mock MCP
server names a tool ``github_close_issue`` (:func:`mcp_name`), and Hermes exposes it as
``mcp__<server>__github_close_issue``; :func:`from_wire_name` maps that form back as well.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ratchet.models import SideEffectClass

__all__ = [
    "ToolSpec",
    "CATALOG",
    "LOCAL_TOOLS",
    "get_tool",
    "wire_name",
    "from_wire_name",
    "mcp_name",
    "from_mcp_name",
    "MCP_PREFIX",
    "openai_tools",
]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: dict[str, object]
    required: tuple[str, ...]
    side_effect: SideEffectClass
    local: bool = False
    world_dependent: bool = False  # results reflect a world that moves
    tags: tuple[str, ...] = field(default_factory=tuple)


def _s(desc: str) -> dict[str, object]:
    return {"type": "string", "description": desc}


def _i(desc: str) -> dict[str, object]:
    return {"type": "integer", "description": desc}


def _arr(desc: str, items: dict[str, object] | None = None) -> dict[str, object]:
    return {"type": "array", "description": desc, "items": items or {"type": "string"}}


_TOOLS: tuple[ToolSpec, ...] = (
    # --- GitHub (fictional demo org) ------------------------------------------------------
    ToolSpec(
        "github.list_issues",
        "List issues in a repository.",
        {
            "repo": _s("owner/name"),
            "state": _s("open | closed | all"),
            "labels": _arr("only issues with all of these labels"),
        },
        ("repo",),
        "read",
    ),
    ToolSpec(
        "github.label",
        "Add labels to an issue.",
        {"repo": _s("owner/name"), "issue": _i("issue number"), "labels": _arr("labels to add")},
        ("repo", "issue", "labels"),
        "write_reversible",
    ),
    ToolSpec(
        "github.comment",
        "Post a comment on an issue.",
        {"repo": _s("owner/name"), "issue": _i("issue number"), "body": _s("markdown")},
        ("repo", "issue", "body"),
        "write_irreversible",
    ),
    ToolSpec(
        "github.close_issue",
        "Close an issue.",
        {
            "repo": _s("owner/name"),
            "issue": _i("issue number"),
            "reason": _s("completed | not_planned"),
        },
        ("repo", "issue"),
        "write_irreversible",
    ),
    ToolSpec(
        "github.open_pr",
        "Open a pull request.",
        {
            "repo": _s("owner/name"),
            "title": _s("title"),
            "body": _s("description"),
            "head": _s("source branch"),
            "base": _s("target branch"),
        },
        ("repo", "title", "head"),
        "write_irreversible",
    ),
    ToolSpec(
        "github.merge_pr",
        "Merge a pull request.",
        {"repo": _s("owner/name"), "pr": _i("pull request number")},
        ("repo", "pr"),
        "write_irreversible",
    ),
    # --- Mail, chat ----------------------------------------------------------------------
    ToolSpec(
        "gmail.send",
        "Send an email.",
        {
            "to": _arr("recipients"),
            "subject": _s("subject"),
            "body": _s("plain text"),
            "cc": _arr("cc recipients"),
        },
        ("to", "subject", "body"),
        "external_send",
    ),
    ToolSpec(
        "gmail.search", "Search the mailbox.", {"query": _s("search query")}, ("query",), "read"
    ),
    ToolSpec(
        "slack.post",
        "Post a message to a Slack channel.",
        {"channel": _s("#channel"), "text": _s("message")},
        ("channel", "text"),
        "external_send",
    ),
    # --- Expenses, invoices, sheets ------------------------------------------------------
    ToolSpec(
        "receipts.parse",
        "Parse a receipt image or PDF into structured lines.",
        {"file": _s("receipt file name")},
        ("file",),
        "read",
    ),
    ToolSpec(
        "sheets.append",
        "Append rows to a spreadsheet.",
        {
            "sheet": _s("sheet name"),
            "rows": _arr("rows", {"type": "array", "items": {"type": "string"}}),
        },
        ("sheet", "rows"),
        "write_reversible",
    ),
    ToolSpec(
        "sheets.read",
        "Read rows from a spreadsheet.",
        {"sheet": _s("sheet name")},
        ("sheet",),
        "read",
    ),
    ToolSpec(
        "finance.rate",
        "Exchange rate between two currencies on a date.",
        {
            "base": _s("ISO currency, e.g. EUR"),
            "quote": _s("ISO currency, e.g. USD"),
            "date": _s("YYYY-MM-DD; defaults to today"),
        },
        ("base", "quote"),
        "read",
        world_dependent=True,
    ),
    ToolSpec(
        "payments.pay",
        "Pay an invoice.",
        {"invoice": _s("invoice id"), "amount": _s("amount"), "currency": _s("ISO")},
        ("invoice", "amount"),
        "spend",
    ),
    # --- Calendar ------------------------------------------------------------------------
    ToolSpec(
        "calendar.freebusy",
        "Busy intervals for attendees in a window.",
        {"attendees": _arr("emails"), "start": _s("ISO start"), "end": _s("ISO end")},
        ("attendees", "start", "end"),
        "read",
    ),
    ToolSpec(
        "calendar.create",
        "Create a calendar event and invite attendees.",
        {
            "title": _s("title"),
            "start": _s("ISO start"),
            "end": _s("ISO end"),
            "attendees": _arr("emails"),
            "calendar": _s("calendar name"),
        },
        ("title", "start", "end"),
        "write_irreversible",
    ),
    # --- Code, web, time -----------------------------------------------------------------
    ToolSpec(
        "git.log",
        "Commit log of a repository.",
        {"repo": _s("repository path or name"), "since": _s("tag or ISO date")},
        ("repo",),
        "read",
    ),
    ToolSpec(
        "web.search",
        "Search the web.",
        {"query": _s("query")},
        ("query",),
        "read",
        world_dependent=True,
    ),
    ToolSpec(
        "web.fetch",
        "Fetch a web page as text.",
        {"url": _s("URL")},
        ("url",),
        "read",
        world_dependent=True,
    ),
    ToolSpec("time.now", "Current date and time (UTC).", {}, (), "read", world_dependent=True),
    # --- Local tools: executed inside the sandbox, reconciled by workspace diff -----------
    ToolSpec(
        "fs.read",
        "Read a file from the workspace.",
        {"path": _s("relative path")},
        ("path",),
        "read",
        local=True,
    ),
    ToolSpec(
        "fs.list",
        "List files in a workspace directory.",
        {"path": _s("relative dir")},
        (),
        "read",
        local=True,
    ),
    ToolSpec(
        "fs.write",
        "Write a file in the workspace.",
        {"path": _s("relative path"), "content": _s("file content")},
        ("path", "content"),
        "write_reversible",
        local=True,
    ),
    ToolSpec(
        "fs.delete",
        "Delete a file in the workspace.",
        {"path": _s("relative path")},
        ("path",),
        "destructive",
        local=True,
    ),
    ToolSpec(
        "shell.run",
        "Run a read-only shell command in the workspace (allowlisted).",
        {"cmd": _s("command line")},
        ("cmd",),
        "read",
        local=True,
    ),
)

CATALOG: dict[str, ToolSpec] = {tool.name: tool for tool in _TOOLS}
LOCAL_TOOLS: frozenset[str] = frozenset(t.name for t in _TOOLS if t.local)


def get_tool(name: str) -> ToolSpec | None:
    return CATALOG.get(name)


def wire_name(name: str) -> str:
    return name.replace(".", "__")


MCP_PREFIX = "mcp__"  # Hermes: mcp__<server>__<mcp name>


def mcp_name(name: str) -> str:
    """``github.close_issue`` → ``github_close_issue`` (the mock MCP server's tool name)."""
    return name.replace(".", "_")


_BY_MCP_NAME: dict[str, str] = {mcp_name(t.name): t.name for t in _TOOLS if not t.local}


def from_mcp_name(name: str) -> str | None:
    """``github_close_issue`` → ``github.close_issue``; ``None`` for a name the server lacks."""
    return _BY_MCP_NAME.get(name)


def from_wire_name(wire: str) -> str:
    """``github__close_issue`` (or Hermes's ``mcp__<server>__github_close_issue``) → dotted."""
    if wire.startswith(MCP_PREFIX):
        _, sep, tool = wire[len(MCP_PREFIX) :].partition("__")
        dotted = from_mcp_name(tool) if sep else None
        if dotted is not None:
            return dotted
    return wire.replace("__", ".")


def openai_tools(names: list[str] | None = None) -> list[dict[str, object]]:
    """OpenAI function definitions for ``names`` (default: the whole catalog)."""
    selected = [CATALOG[n] for n in names] if names is not None else list(_TOOLS)
    return [
        {
            "type": "function",
            "function": {
                "name": wire_name(tool.name),
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": tool.params,
                    "required": list(tool.required),
                    "additionalProperties": False,
                },
            },
        }
        for tool in selected
    ]
