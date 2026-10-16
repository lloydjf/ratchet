"""args_norm drops volatile fields and sorts keys; cassette keys."""

from __future__ import annotations

import re

import pytest

from ratchet.mocktools.normalize import (
    args_hash,
    cassette_key,
    is_volatile_key,
    load_rules,
    normalize_args,
    parse_rules,
)
from ratchet.util import canonical_json, sha256_hex

# 20 pairs of calls that must hash identically.
EQUIVALENT: list[tuple[str, dict[str, object], dict[str, object]]] = [
    (
        "github.list_issues",
        {"repo": "acme/widgets", "state": "open"},
        {"state": "open", "repo": "acme/widgets"},
    ),
    (
        "github.list_issues",
        {"repo": "acme/widgets", "request_id": "r-1"},
        {"repo": "acme/widgets", "request_id": "r-2"},
    ),
    (
        "github.list_issues",
        {"repo": "acme/widgets", "labels": ["bug", "p1"]},
        {"repo": "acme/widgets", "labels": ["p1", "bug"]},
    ),
    (
        "github.label",
        {"repo": "acme/widgets", "issue": 12, "labels": ["stale"], "nonce": "a"},
        {"labels": ["stale"], "issue": 12, "repo": "acme/widgets", "nonce": "b"},
    ),
    (
        "github.comment",
        {"repo": "acme/widgets", "issue": 12, "body": "hi", "ts": 1},
        {"repo": "acme/widgets", "issue": 12, "body": "hi", "ts": 2},
    ),
    (
        "github.close_issue",
        {
            "repo": "acme/widgets",
            "issue": 13,
            "reason": "completed",
            "timestamp": "2026-10-14T09:00:00Z",
        },
        {"repo": "acme/widgets", "issue": 13, "reason": "completed"},
    ),
    (
        "github.open_pr",
        {"repo": "acme/widgets", "title": "Fix", "head": "fix/x", "etag": "W/1"},
        {"head": "fix/x", "title": "Fix", "repo": "acme/widgets"},
    ),
    (
        "github.merge_pr",
        {"repo": "acme/widgets", "pr": 15, "idempotency_key": "k1"},
        {"repo": "acme/widgets", "pr": 15, "idempotency_key": "k2"},
    ),
    (
        "gmail.send",
        {"to": ["a@example.com", "b@example.com"], "subject": "s", "body": "b"},
        {"to": ["b@example.com", "a@example.com"], "subject": "s", "body": "b"},
    ),
    (
        "gmail.send",
        {"to": ["a@example.com"], "subject": "s", "body": "b", "cc": None},
        {"to": ["a@example.com"], "subject": "s", "body": "b"},
    ),
    (
        "gmail.search",
        {"query": "from:alice", "page_token": "p1"},
        {"query": "from:alice", "page_token": "p2"},
    ),
    (
        "slack.post",
        {"channel": "#ops", "text": "done", "client_ref": "x"},
        {"text": "done", "channel": "#ops"},
    ),
    (
        "receipts.parse",
        {"file": "taxi.pdf", "upload_id": "u-1"},
        {"file": "taxi.pdf", "upload_id": "u-9"},
    ),
    (
        "sheets.append",
        {"sheet": "Expenses", "rows": [["a", "b"]], "requestId": "R1"},
        {"rows": [["a", "b"]], "sheet": "Expenses", "requestId": "R2"},
    ),
    (
        "sheets.read",
        {"sheet": "Expenses", "meta": {"trace_id": "t1", "page": 1}},
        {"sheet": "Expenses", "meta": {"page": 1, "trace_id": "t2"}},
    ),
    (
        "finance.rate",
        {"base": "EUR", "quote": "USD", "date": "2026-10-14", "cache_bust": 1},
        {"quote": "USD", "base": "EUR", "date": "2026-10-14"},
    ),
    (
        "payments.pay",
        {"invoice": "INV-1", "amount": "10.00", "session_token": "abc"},
        {"amount": "10.00", "invoice": "INV-1"},
    ),
    (
        "calendar.freebusy",
        {"attendees": ["b@example.com", "a@example.com"], "start": "s", "end": "e"},
        {"attendees": ["a@example.com", "b@example.com"], "end": "e", "start": "s"},
    ),
    (
        "calendar.create",
        {"title": "Sync", "start": "s", "end": "e", "event_id": "evt_1"},
        {"title": "Sync", "start": "s", "end": "e"},
    ),
    (
        "web.fetch",
        {"url": "https://example.com/a", "fetchedAtTs": 123},
        {"url": "https://example.com/a"},
    ),
]

# Pairs that differ in something meaningful and must NOT hash identically.
DIFFERENT: list[tuple[str, dict[str, object], dict[str, object]]] = [
    (
        "github.close_issue",
        {"repo": "acme/widgets", "issue": 12},
        {"repo": "acme/widgets", "issue": 13},
    ),
    (
        "github.label",
        {"repo": "acme/widgets", "issue": 12, "labels": ["bug"]},
        {"repo": "acme/widgets", "issue": 12, "labels": ["stale"]},
    ),
    (
        "gmail.send",
        {"to": ["a@example.com"], "subject": "s", "body": "b"},
        {"to": ["a@example.com"], "subject": "s", "body": "b "},
    ),
    (
        "gmail.send",
        {"to": ["a@example.com"], "subject": "S", "body": "b"},
        {"to": ["a@example.com"], "subject": "s", "body": "b"},
    ),
    (
        "sheets.append",
        {"sheet": "Expenses", "rows": [["a", "b"]]},
        {"sheet": "Expenses", "rows": [["b", "a"]]},
    ),
    (
        "finance.rate",
        {"base": "EUR", "quote": "USD", "date": "2026-10-13"},
        {"base": "EUR", "quote": "USD", "date": "2026-10-14"},
    ),
    (
        "payments.pay",
        {"invoice_id": "INV-1", "amount": "1"},
        {"invoice_id": "INV-2", "amount": "1"},
    ),
    ("github.list_issues", {"repo": "acme/widgets"}, {"repo": "acme/gadgets"}),
]


@pytest.mark.parametrize(("tool", "left", "right"), EQUIVALENT)
def test_equivalent_calls_hash_identically(
    tool: str, left: dict[str, object], right: dict[str, object]
) -> None:
    a, b = normalize_args(tool, left), normalize_args(tool, right)
    assert a == b
    assert cassette_key(tool, a) == cassette_key(tool, b)


@pytest.mark.parametrize(("tool", "left", "right"), DIFFERENT)
def test_meaningfully_different_calls_hash_differently(
    tool: str, left: dict[str, object], right: dict[str, object]
) -> None:
    assert cassette_key(tool, normalize_args(tool, left)) != cassette_key(
        tool, normalize_args(tool, right)
    )


def test_twenty_equivalence_fixtures_exist() -> None:
    assert len(EQUIVALENT) == 20


def test_same_args_under_different_tools_hash_differently() -> None:
    args = {"repo": "acme/widgets"}
    assert cassette_key("github.list_issues", args) != cassette_key("git.log", args)


def test_cassette_key_matches_the_doc_formula() -> None:
    norm = normalize_args("github.close_issue", {"repo": "acme/widgets", "issue": 12})
    expected = sha256_hex("github.close_issue\n" + canonical_json(norm))
    assert cassette_key("github.close_issue", norm) == expected
    assert args_hash("github.close_issue", norm) == expected
    assert re.fullmatch(r"[0-9a-f]{64}", expected)


def test_keys_are_sorted_recursively() -> None:
    norm = normalize_args("sheets.read", {"sheet": "S", "meta": {"z": 1, "a": {"y": 2, "b": 3}}})
    assert list(norm) == ["meta", "sheet"]
    meta = norm["meta"]
    assert isinstance(meta, dict)
    assert list(meta) == ["a", "z"]
    assert list(meta["a"]) == ["b", "y"]


def test_global_pattern_keeps_semantic_keys() -> None:
    norm = normalize_args(
        "github.label",
        {"repo": "acme/widgets", "issue": 12, "labels": ["x"], "paid": True, "valid": 1},
    )
    assert set(norm) == {"repo", "issue", "labels", "paid", "valid"}


@pytest.mark.parametrize(
    "key", ["id", "issue_id", "request_ts", "timestamp", "nonce", "page_token", "etag", "userId"]
)
def test_global_pattern_drops_volatile_key_names(key: str) -> None:
    rules = load_rules()
    assert is_volatile_key(key, rules.for_tool("github.comment"), rules.pattern)


def test_per_tool_keep_protects_semantic_ids() -> None:
    a = normalize_args("payments.pay", {"invoice_id": "INV-1", "amount": "1"})
    assert a["invoice_id"] == "INV-1"
    b = normalize_args("calendar.create", {"title": "t", "calendar_id": "team"})
    assert b["calendar_id"] == "team"
    # The same key on a tool without `keep` is volatile.
    c = normalize_args("github.comment", {"invoice_id": "INV-1", "body": "x"})
    assert "invoice_id" not in c


def test_exact_key_does_not_normalize_whitespace_or_case() -> None:
    a = normalize_args("gmail.search", {"query": "from:alice"})
    b = normalize_args("gmail.search", {"query": " From:alice"})
    assert a != b


def test_tokenizer_hook_runs_after_volatile_drop_on_values_and_keys() -> None:
    seen: list[object] = []

    def tokenizer(value: object) -> object:
        seen.append(value)
        return "<EMAIL_1>" if value == "a@example.com" else value

    norm = normalize_args(
        "gmail.search",
        {"query": "a@example.com", "nonce": "x", "by_owner": {"a@example.com": 1}},
        tokenizer=tokenizer,
    )
    assert norm == {"by_owner": {"<EMAIL_1>": 1}, "query": "<EMAIL_1>"}
    assert "x" not in seen  # the volatile nonce was dropped before tokenizing


def test_rules_parse_from_a_document() -> None:
    rules = parse_rules({"global_pattern": "^secret$", "tools": {"x.y": {"drop": ["q"]}}})
    norm = normalize_args("x.y", {"q": 1, "secret": 2, "id": 3}, rules=rules)
    assert norm == {"id": 3}
