"""The fictional world and its record-mode adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ratchet.errors import ConfigError, ConflictError, NotFoundError
from ratchet.mocktools.adapters import ADAPTERS, get_adapter
from ratchet.mocktools.adapters.comms import message_matches
from ratchet.mocktools.adapters.money import lookup_rate
from ratchet.mocktools.world import CallContext, ToolArgumentError, World, WorldState
from ratchet.mocktools.world_models import Message
from ratchet.tools.catalog import CATALOG

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "world" / "sample"
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture
def world() -> World:
    return World.load(SAMPLE)


def run(
    world: World, tool: str, args: dict[str, object], run_id: str = "run_a"
) -> dict[str, object]:
    adapter = get_adapter(tool)
    assert adapter is not None
    return adapter(world.state(run_id), args, CallContext(run_id=run_id, now=NOW))


def state(world: World, run_id: str = "run_a") -> WorldState:
    return world.state(run_id)


def test_every_non_local_catalog_tool_has_an_adapter() -> None:
    served = {name for name, spec in CATALOG.items() if not spec.local}
    assert set(ADAPTERS) == served


def test_world_loads_manifest_and_frozen_time(world: World) -> None:
    assert world.name == "sample"
    assert world.frozen_time == NOW


def test_missing_world_directory_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        World.load(tmp_path / "nope")


def test_invalid_fixture_is_rejected_with_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "github.json").write_text(json.dumps({"repos": {}, "typo": 1}))
    with pytest.raises(ConfigError) as info:
        World.load(tmp_path)
    assert info.value.context == {"file": "github.json"}


def test_empty_directory_is_an_empty_world(tmp_path: Path) -> None:
    empty = World.load(tmp_path)
    with pytest.raises(NotFoundError):
        run(empty, "github.list_issues", {"repo": "acme/widgets"})


def test_list_issues_filters_state_and_labels(world: World) -> None:
    result = run(world, "github.list_issues", {"repo": "acme/widgets"})
    assert [i["number"] for i in result["issues"]] == [11, 12, 13]  # type: ignore[index, union-attr]
    closed = run(world, "github.list_issues", {"repo": "acme/widgets", "state": "closed"})
    assert [i["number"] for i in closed["issues"]] == [14]  # type: ignore[index, union-attr]
    bugs = run(world, "github.list_issues", {"repo": "acme/widgets", "labels": ["bug"]})
    assert [i["number"] for i in bugs["issues"]] == [11]  # type: ignore[index, union-attr]


def test_close_issue_mutates_the_runs_state_only(world: World) -> None:
    result = run(world, "github.close_issue", {"repo": "acme/widgets", "issue": 13})
    assert result["state"] == "closed"
    assert result["state_reason"] == "completed"
    issue = next(i for i in state(world).github.repos["acme/widgets"].issues if i.number == 13)
    assert issue.state == "closed" and issue.closed_at == "2026-10-14T09:00:00Z"
    still_open = run(world, "github.list_issues", {"repo": "acme/widgets"})
    assert [i["number"] for i in still_open["issues"]] == [11, 12]  # type: ignore[index, union-attr]
    other = run(world, "github.list_issues", {"repo": "acme/widgets"}, run_id="run_b")
    assert [i["number"] for i in other["issues"]] == [11, 12, 13]  # type: ignore[index, union-attr]


def test_reset_forgets_a_runs_mutations(world: World) -> None:
    run(world, "github.close_issue", {"repo": "acme/widgets", "issue": 13})
    world.reset("run_a")
    assert len(run(world, "github.list_issues", {"repo": "acme/widgets"})["issues"]) == 3  # type: ignore[arg-type]


def test_label_and_comment_update_the_issue(world: World) -> None:
    labelled = run(
        world,
        "github.label",
        {"repo": "acme/widgets", "issue": "#12", "labels": ["stale", "stale"]},
    )
    assert labelled["labels"] == ["stale"]
    commented = run(world, "github.comment", {"repo": "acme/widgets", "issue": 12, "body": "ping"})
    assert commented["comment_id"] == 1002


def test_unknown_issue_is_not_found(world: World) -> None:
    with pytest.raises(NotFoundError):
        run(world, "github.close_issue", {"repo": "acme/widgets", "issue": 99})


def test_open_and_merge_pr(world: World) -> None:
    opened = run(world, "github.open_pr", {"repo": "acme/widgets", "title": "Bump", "head": "deps"})
    assert opened["pr"] == 16
    merged = run(world, "github.merge_pr", {"repo": "acme/widgets", "pr": 16})
    assert merged["merged"] is True
    with pytest.raises(ConflictError):
        run(world, "github.merge_pr", {"repo": "acme/widgets", "pr": 16})


def test_gmail_send_appends_to_outbox_and_search_finds_inbox(world: World) -> None:
    sent = run(world, "gmail.send", {"to": "alice@example.com", "subject": "Hi", "body": "Thanks"})
    assert sent == {"message_id": "msg_out_0001", "status": "sent", "to": ["alice@example.com"]}
    assert state(world).mailbox.outbox[0].sender == "agent@example.com"
    found = run(world, "gmail.search", {"query": "from:alice receipts"})
    assert [m["message_id"] for m in found["messages"]] == ["msg_0001"]  # type: ignore[index, union-attr]


def test_message_query_terms() -> None:
    msg = Message.model_validate(
        {
            "message_id": "m",
            "from": "bob@example.com",
            "to": ["x@example.com"],
            "subject": "Sync",
            "body": "Thursday",
            "date": "d",
            "labels": ["IMPORTANT"],
        }
    )
    assert message_matches(msg, "label:important thursday")
    assert not message_matches(msg, "from:alice")


def test_slack_post_appends(world: World) -> None:
    result = run(world, "slack.post", {"channel": "ops", "text": "done"})
    assert result == {"channel": "#ops", "posted": True, "message_number": 2}


def test_receipt_parse_by_name_or_stem(world: World) -> None:
    taxi = run(world, "receipts.parse", {"file": "taxi-2026-10-01.pdf"})
    assert taxi["vat"] == 7.98 and taxi["total"] == 50.0
    hotel = run(world, "receipts.parse", {"file": "hotel-2026-10-02.pdf"})
    assert hotel["merchant"] == "Hotel Example Mitte"
    with pytest.raises(ToolArgumentError):
        run(world, "receipts.parse", {"file": "%bad*name"})
    with pytest.raises(NotFoundError):
        run(world, "receipts.parse", {"file": "missing.pdf"})


def test_sheets_append_then_read(world: World) -> None:
    appended = run(
        world,
        "sheets.append",
        {"sheet": "Expenses", "rows": [["2026-10-01", "City Taxi", "EUR", 42.02, 7.98, 50]]},
    )
    assert appended == {"sheet": "Expenses", "appended": 1, "total_rows": 2}
    rows = run(world, "sheets.read", {"sheet": "Expenses"})["rows"]
    assert rows[-1] == ["2026-10-01", "City Taxi", "EUR", "42.02", "7.98", "50"]  # type: ignore[index]
    created = run(world, "sheets.append", {"sheet": "New", "rows": ["a", "b"]})
    assert created["total_rows"] == 1


def test_fx_lookup_exact_previous_inverse_and_default_date(world: World) -> None:
    assert (
        run(world, "finance.rate", {"base": "EUR", "quote": "USD", "date": "2026-10-13"})["rate"]
        == 1.0812
    )
    assert run(world, "finance.rate", {"base": "eur", "quote": "usd"})["rate"] == 1.0842
    later = run(world, "finance.rate", {"base": "EUR", "quote": "USD", "date": "2026-10-20"})
    assert later["date"] == "2026-10-14"
    inverse = run(world, "finance.rate", {"base": "USD", "quote": "EUR", "date": "2026-10-14"})
    assert inverse["rate"] == round(1 / 1.0842, 6)
    with pytest.raises(NotFoundError):
        run(world, "finance.rate", {"base": "EUR", "quote": "USD", "date": "2026-01-01"})
    with pytest.raises(ToolArgumentError):
        run(world, "finance.rate", {"base": "EURO", "quote": "USD"})


def test_lookup_rate_same_currency_is_one() -> None:
    assert lookup_rate({}, "EUR", "EUR", "2026-10-14") == ("2026-10-14", 1.0)


def test_payments_pay_marks_invoice_paid(world: World) -> None:
    paid = run(world, "payments.pay", {"invoice": "INV-1001", "amount": "250.00"})
    assert (
        paid["status"] == "paid" and paid["payment_ref"] == "pay_0001" and paid["currency"] == "EUR"
    )
    with pytest.raises(ConflictError):
        run(world, "payments.pay", {"invoice": "INV-1001", "amount": "250.00"})
    with pytest.raises(NotFoundError):
        run(world, "payments.pay", {"invoice": "INV-9", "amount": "1"})


def test_calendar_freebusy_and_create(world: World) -> None:
    window = {"start": "2026-10-15T08:00:00Z", "end": "2026-10-15T18:00:00Z"}
    busy = run(
        world,
        "calendar.freebusy",
        {"attendees": ["bob@example.com", "carol@example.com"], **window},
    )
    assert busy["busy"] == {
        "bob@example.com": [
            {"start": "2026-10-15T09:00:00Z", "end": "2026-10-15T09:30:00Z"},
            {"start": "2026-10-15T13:00:00Z", "end": "2026-10-15T15:00:00Z"},
        ],
        "carol@example.com": [],
    }
    created = run(
        world,
        "calendar.create",
        {
            "title": "Planning",
            "start": "2026-10-15T10:00:00Z",
            "end": "2026-10-15T10:30:00Z",
            "attendees": ["carol@example.com"],
        },
    )
    assert created["event_id"] == "evt_0002"
    again = run(world, "calendar.freebusy", {"attendees": ["carol@example.com"], **window})
    assert again["busy"] == {
        "carol@example.com": [{"start": "2026-10-15T10:00:00Z", "end": "2026-10-15T10:30:00Z"}]
    }
    with pytest.raises(ToolArgumentError):
        run(
            world,
            "calendar.create",
            {"title": "Bad", "start": "2026-10-15T10:00:00Z", "end": "2026-10-15T09:00:00Z"},
        )


def test_git_log_since_tag_and_date(world: World) -> None:
    by_tag = run(world, "git.log", {"repo": "widgets", "since": "v1.4.0"})
    assert [c["sha"] for c in by_tag["commits"]] == ["c3d4e5f", "b2c3d4e"]  # type: ignore[index, union-attr]
    by_date = run(world, "git.log", {"repo": "acme/widgets", "since": "2026-09-25"})
    assert len(by_date["commits"]) == 3  # type: ignore[arg-type]


def test_web_search_canned_and_fallback_and_fetch(world: World) -> None:
    canned = run(world, "web.search", {"query": "  EUR USD rate today "})
    assert canned["results"][0]["url"] == "https://news.example.com/fx/eur-usd"  # type: ignore[index]
    fallback = run(world, "web.search", {"query": "dark mode"})
    assert [r["url"] for r in fallback["results"]] == ["https://blog.example.com/widgets-2-0"]  # type: ignore[index, union-attr]
    page = run(world, "web.fetch", {"url": "https://news.example.com/fx/eur-usd/"})
    assert "1.0842" in str(page["text"])
    with pytest.raises(NotFoundError):
        run(world, "web.fetch", {"url": "https://nowhere.example.com"})


def test_time_now_reports_the_call_clock(world: World) -> None:
    assert run(world, "time.now", {}) == {
        "now": "2026-10-14T09:00:00Z",
        "date": "2026-10-14",
        "weekday": "Wednesday",
        "timezone": "UTC",
        "epoch_s": int(NOW.timestamp()),
    }
