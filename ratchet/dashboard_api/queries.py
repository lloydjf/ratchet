"""Read-only queries behind the list routes: opaque cursors, session visibility, paging.

Every list is newest first and keyed by ``(created_at, id)``. A cursor is that pair as
URL-safe base64 JSON; clients treat it as opaque. Visibility: a caller
sees rows of its own session plus global rows (``session_hash IS NULL``), never another
session's.
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from dataclasses import dataclass

from ratchet.dashboard_api.errors import BadRequest
from ratchet.models import Event
from ratchet.store import Store

__all__ = [
    "MAX_LIMIT",
    "encode_cursor",
    "decode_cursor",
    "visible_clause",
    "PageRows",
    "verdict_rows",
    "edit_rows",
    "trace_rows",
    "event_page",
    "visible_verdict_ids",
    "edit_for_verdict",
    "TERMINAL_STATES",
]

MAX_LIMIT = 200
TERMINAL_STATES = frozenset({"done", "failed_infra"})


def encode_cursor(created_at: str, key: str | int) -> str:
    raw = json.dumps([created_at, key], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str | int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise BadRequest("The cursor is not valid.") from exc
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not isinstance(value[1], str | int)
    ):
        raise BadRequest("The cursor is not valid.")
    return value[0], value[1]


def visible_clause(
    session_hash: str | None, column: str = "session_hash"
) -> tuple[str, tuple[object, ...]]:
    if session_hash is None:
        return f"{column} IS NULL", ()
    return f"({column} IS NULL OR {column} = ?)", (session_hash,)


@dataclass(frozen=True)
class PageRows:
    rows: list[sqlite3.Row]
    next_cursor: str | None


def _page(
    store: Store,
    *,
    table: str,
    time_col: str,
    key_col: str,
    where: list[str],
    params: tuple[object, ...],
    cursor: str | None,
    limit: int,
    columns: str = "*",
) -> PageRows:
    clauses = list(where)
    values: list[object] = list(params)
    if cursor is not None:
        at, key = decode_cursor(cursor)
        clauses.append(f"({time_col}, {key_col}) < (?, ?)")
        values.extend([at, str(key)])
    sql = (
        f"SELECT {columns} FROM {table} WHERE {' AND '.join(clauses) or '1=1'} "
        f"ORDER BY {time_col} DESC, {key_col} DESC LIMIT ?"
    )
    rows = store.query(sql, (*values, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = None
    if more and rows:
        last = rows[-1]
        nxt = encode_cursor(str(last[time_col.split(".")[-1]]), str(last[key_col.split(".")[-1]]))
    return PageRows(rows, nxt)


def verdict_rows(
    store: Store, session_hash: str | None, cursor: str | None, limit: int
) -> PageRows:
    clause, params = visible_clause(session_hash)
    return _page(
        store,
        table="verdicts",
        time_col="created_at",
        key_col="verdict_id",
        where=[clause],
        params=params,
        cursor=cursor,
        limit=limit,
    )


def edit_rows(store: Store, session_hash: str | None, cursor: str | None, limit: int) -> PageRows:
    clause, params = visible_clause(session_hash)
    return _page(
        store,
        table="edits",
        time_col="detected_at",
        key_col="edit_id",
        where=[clause],
        params=params,
        cursor=cursor,
        limit=limit,
    )


def trace_rows(
    store: Store,
    *,
    kind: str,
    skill: str | None,
    search: str | None,
    cursor: str | None,
    limit: int,
) -> PageRows:
    where = ["t.kind = ?", "t.archived_reason IS NULL"]
    params: list[object] = [kind]
    if skill:
        where.append("t.run_id IN (SELECT run_id FROM trace_skills WHERE skill = ?)")
        params.append(skill)
    if search:
        where.append("(t.task_label LIKE ? ESCAPE '\\' OR t.run_id = ?)")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%", search])
    return _page(
        store,
        table="traces t",
        time_col="t.created_at",
        key_col="t.run_id",
        where=where,
        params=tuple(params),
        cursor=cursor,
        limit=limit,
        columns="t.run_id, t.created_at, t.last_replayed_at",
    )


def event_page(
    store: Store,
    *,
    session_hash: str | None,
    since: int | None,
    kinds: list[str],
    cursor: str | None,
    limit: int,
) -> tuple[list[Event], str | None]:
    """Without ``since``: newest first, the cursor walks back in time. With ``since``
    (polling fallback): oldest first after ``since``, the cursor walks forward."""
    key = int(decode_cursor(cursor)[1]) if cursor is not None else None
    include_global = session_hash is not None  # see sse.event_frames
    if since is not None:
        rows = store.events(
            since=max(since, key) if key is not None else since,
            kinds=kinds or None,
            session_hash=session_hash,
            include_global=include_global,
            limit=limit,
        )
    else:
        rows = store.events(
            since=0,
            kinds=kinds or None,
            session_hash=session_hash,
            include_global=include_global,
            limit=limit,
            newest_first=True,
            before=key,
        )
    nxt = encode_cursor(rows[-1].ts.isoformat(), rows[-1].id) if len(rows) == limit else None
    return rows, nxt


def visible_verdict_ids(
    store: Store, session_hash: str | None, *, own_only: bool = False, cap: int = 500
) -> list[str]:
    clause: str
    params: tuple[object, ...]
    if own_only:
        if session_hash is None:
            return []
        clause, params = "session_hash = ?", (session_hash,)
    else:
        clause, params = visible_clause(session_hash)
    rows = store.query(
        f"SELECT verdict_id FROM verdicts WHERE {clause} ORDER BY created_at DESC LIMIT ?",
        (*params, cap),
    )
    return [str(r["verdict_id"]) for r in rows]


def edit_for_verdict(store: Store, verdict_id: str) -> str | None:
    """The edit behind a verdict id, before or after the verdict is saved (§2 pre-allocation)."""
    row = store.query_one(
        "SELECT edit_id FROM verdicts WHERE verdict_id = ? UNION ALL "
        "SELECT edit_id FROM edits WHERE verdict_id = ? LIMIT 1",
        (verdict_id, verdict_id),
    )
    if row is not None:
        return str(row["edit_id"])
    if verdict_id.startswith("verdict_"):
        candidate = "edit_" + verdict_id.removeprefix("verdict_")
        if store.query_one("SELECT 1 FROM edits WHERE edit_id = ?", (candidate,)) is not None:
            return candidate
    return None
