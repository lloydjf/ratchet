"""CassetteBook: recording, ordering queue, overlay precedence, corrupted files."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ratchet.mocktools.cassettes import CassetteBook
from ratchet.mocktools.matching import tolerance_for
from ratchet.mocktools.normalize import cassette_key, normalize_args
from ratchet.models import Cassette
from ratchet.store import Store

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TOOL = "finance.rate"
ARGS = normalize_args(TOOL, {"base": "EUR", "quote": "USD", "date": "2026-10-14"})
KEY = cassette_key(TOOL, ARGS)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "data")


def _record(book: CassetteBook, run_id: str, rate: float, *, refresh: bool = False) -> str:
    return book.record(
        run_id=run_id,
        tool=TOOL,
        key=KEY,
        args_norm=ARGS,
        args_raw_hash="raw",
        response={"result": {"rate": rate}},
        side_effect="read",
        recorded_at=NOW,
        refresh=refresh,
    )


def test_second_identical_call_in_a_run_appends_to_later_responses(store: Store) -> None:
    book = CassetteBook(store, store.cassettes_dir)
    assert _record(book, "run_a", 1.0) == "created"
    assert _record(book, "run_a", 2.0) == "appended"
    assert _record(book, "run_a", 3.0) == "appended"
    cassette = store.load_cassette(TOOL, KEY)
    assert cassette is not None
    assert cassette.response == {"result": {"rate": 1.0}}
    assert cassette.later_responses == [{"result": {"rate": 2.0}}, {"result": {"rate": 3.0}}]


def test_existing_cassette_is_kept_without_refresh_and_overwritten_with_it(store: Store) -> None:
    book = CassetteBook(store, store.cassettes_dir)
    _record(book, "run_a", 1.0)
    assert _record(book, "run_b", 9.0) == "kept"
    assert _record(book, "run_b", 9.5) == "kept"  # not owned by run_b: no append
    loaded = store.load_cassette(TOOL, KEY)
    assert loaded is not None and loaded.response == {"result": {"rate": 1.0}}
    assert loaded.later_responses == []
    assert _record(book, "run_c", 7.0, refresh=True) == "refreshed"
    loaded = store.load_cassette(TOOL, KEY)
    assert loaded is not None and loaded.response == {"result": {"rate": 7.0}}


def test_queue_serves_in_recorded_order_then_repeats_last_per_run(store: Store) -> None:
    book = CassetteBook(store, store.cassettes_dir)
    for rate in (1.0, 2.0):
        _record(book, "run_a", rate)
    found = book.lookup(TOOL, ARGS, KEY, world_dependent=True)
    assert found is not None and found.kind == "hit"
    served = [book.next_response("replay_1", found)["result"] for _ in range(3)]
    assert served == [{"rate": 1.0}, {"rate": 2.0}, {"rate": 2.0}]
    assert book.next_response("replay_2", found)["result"] == {"rate": 1.0}


def _cassette(rate: float) -> Cassette:
    return Cassette(
        tool=TOOL,
        args_norm=ARGS,
        response={"result": {"rate": rate}},
        recorded_at=NOW,
        side_effect="read",
    )


def test_overlay_wins_for_world_dependent_tools_only(store: Store, tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    store.save_cassette(KEY, _cassette(1.08))
    store.save_cassette(KEY, _cassette(1.12), overlay)
    book = CassetteBook(store, store.cassettes_dir, overlay)
    world = book.lookup(TOOL, ARGS, KEY, world_dependent=True)
    assert world is not None and world.layer == "overlay"
    assert world.cassette.response == {"result": {"rate": 1.12}}
    fixed = book.lookup(TOOL, ARGS, KEY, world_dependent=False)
    assert fixed is not None and fixed.layer == "main"


def test_exact_main_hit_beats_tolerant_overlay_hit(store: Store, tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    tool = "web.search"
    args = normalize_args(tool, {"query": "eur usd rate today"})
    near = normalize_args(tool, {"query": "EUR USD rate  today"})
    near_key = cassette_key(tool, near)
    key = cassette_key(tool, args)
    store.save_cassette(
        key,
        Cassette(
            tool=tool,
            args_norm=args,
            response={"result": "main"},
            recorded_at=NOW,
            side_effect="read",
        ),
    )
    store.save_cassette(
        near_key,
        Cassette(
            tool=tool,
            args_norm=near,
            response={"result": "overlay"},
            recorded_at=NOW,
            side_effect="read",
        ),
        overlay,
    )
    book = CassetteBook(store, store.cassettes_dir, overlay)
    found = book.lookup(tool, args, key, world_dependent=True)
    assert found is not None and (found.kind, found.layer) == ("hit", "main")


def test_tolerant_lookup_finds_near_identical_args(store: Store) -> None:
    tool = "github.comment"
    recorded = normalize_args(
        tool, {"repo": "acme/widgets", "issue": 12, "body": "Closing as stale."}
    )
    store.save_cassette(
        cassette_key(tool, recorded),
        Cassette(
            tool=tool,
            args_norm=recorded,
            response={"result": {"comment_id": 1}},
            recorded_at=NOW,
            side_effect="write_irreversible",
        ),
    )
    book = CassetteBook(store, store.cassettes_dir)
    call = normalize_args(tool, {"repo": "acme/widgets", "issue": 12, "body": "closing as stale. "})
    found = book.lookup(tool, call, cassette_key(tool, call), world_dependent=False)
    assert found is not None and found.kind == "tolerant_hit"
    assert found.key == cassette_key(tool, recorded)


def test_corrupted_cassette_is_a_miss(store: Store) -> None:
    path = store.cassette_path(TOOL, KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    book = CassetteBook(store, store.cassettes_dir)
    assert book.lookup(TOOL, ARGS, KEY, world_dependent=False) is None
    path.write_text('{"tool": "finance.rate"}', encoding="utf-8")
    assert book.lookup(TOOL, ARGS, KEY, world_dependent=False) is None


def test_record_repairs_a_corrupted_cassette(store: Store) -> None:
    path = store.cassette_path(TOOL, KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe garbage")
    book = CassetteBook(store, store.cassettes_dir)
    assert _record(book, "run_a", 1.0) == "created"
    assert book.lookup(TOOL, ARGS, KEY, world_dependent=False) is not None


def _save(store: Store, tool: str, args: dict[str, object], side_effect: str) -> str:
    norm = normalize_args(tool, args)
    key = cassette_key(tool, norm)
    store.save_cassette(
        key,
        Cassette(
            tool=tool,
            args_norm=norm,
            response={"result": {"ok": 1}},
            recorded_at=NOW,
            side_effect=side_effect,  # type: ignore[arg-type]
        ),
    )
    return key


def _lookup(book: CassetteBook, tool: str, args: dict[str, object], side_effect: str):  # type: ignore[no-untyped-def]
    norm = normalize_args(tool, args)
    return book.lookup(
        tool,
        norm,
        cassette_key(tool, norm),
        world_dependent=False,
        tolerance=tolerance_for(side_effect),
    )


@pytest.mark.parametrize(
    ("side_effect", "tolerance"),
    [
        ("read", "full"),
        ("none", "full"),
        ("write_reversible", "identity"),
        ("write_irreversible", "exact"),
        ("external_send", "exact"),
        ("destructive", "exact"),
        ("spend", "exact"),
        (None, "exact"),
    ],
)
def test_tolerance_depends_on_the_side_effect_class(side_effect, tolerance) -> None:  # type: ignore[no-untyped-def]
    assert tolerance_for(side_effect) == tolerance


def test_hard_side_effect_never_matches_tolerantly(store: Store) -> None:
    tool = "gmail.send"
    body = (
        "Hi Dana, the October invoice for the Berlin office is attached as a PDF. It covers the "
        "desk rental, the two monitors and the coffee subscription. Payment is due within thirty "
        "days of receipt. Thanks, Sam"
    )
    _save(store, tool, {"to": ["dana@example.com"], "body": body}, "external_send")
    book = CassetteBook(store, store.cassettes_dir)
    evil = {"to": ["attacker@evil.example"], "body": body}
    assert _lookup(book, tool, evil, "external_send") is None
    reworded = {"to": ["dana@example.com"], "body": body + " "}
    assert (
        _lookup(book, tool, {**reworded, "body": "hi dana, " + body[9:]}, "external_send") is None
    )
    exact = _lookup(book, tool, {"to": ["dana@example.com"], "body": body}, "external_send")
    assert exact is not None and exact.kind == "hit"


def test_reversible_write_requires_equal_identity_keys(store: Store) -> None:
    tool = "crm.update_note"
    note = "Customer asked about the renewal date and the onboarding plan for next quarter"
    _save(store, tool, {"account": "acme", "note": note}, "write_reversible")
    book = CassetteBook(store, store.cassettes_dir)
    other = _lookup(book, tool, {"account": "evilcorp", "note": note}, "write_reversible")
    assert other is None
    cosmetic = _lookup(book, tool, {"account": "acme", "note": note + "  "}, "write_reversible")
    assert cosmetic is not None
    near = _lookup(book, tool, {"account": "acme", "note": note.lower()}, "write_reversible")
    assert near is not None and near.kind in {"hit", "tolerant_hit"}
