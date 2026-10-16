"""Mock tool server over HTTP: record, replay, corrupted cassettes, no raw PII stored."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ratchet.config.settings import Settings
from ratchet.errors import ConfigError
from ratchet.mocktools.app import create_app
from ratchet.mocktools.normalize import cassette_key, normalize_args
from ratchet.mocktools.world import World
from ratchet.models import Cassette, ToolCall
from ratchet.store import Store

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "world" / "sample"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RUN = {"X-Ratchet-Run-Id": "run_rec"}


class StubTokenizer:
    """Deterministic stand-in for ratchet.pii: emails -> <EMAIL_n>."""

    def __init__(self) -> None:
        self.table: dict[str, str] = {}

    def _token(self, match: re.Match[str]) -> str:
        return self.table.setdefault(match.group(0), f"<EMAIL_{len(self.table) + 1}>")

    def __call__(self, value: object) -> object:
        if isinstance(value, str):
            return EMAIL.sub(self._token, value)
        if isinstance(value, dict):
            return {k: self(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        return value


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, ratchet_data_dir=tmp_path / "data")  # type: ignore[call-arg]


@pytest.fixture
def store(settings: Settings) -> Store:
    return Store.open(settings.ratchet_data_dir)


@pytest.fixture
def tokenizer() -> StubTokenizer:
    return StubTokenizer()


AppFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(settings: Settings, store: Store, tokenizer: StubTokenizer) -> Iterator[AppFactory]:
    def build(mode: str, **kwargs: object) -> TestClient:
        world = kwargs.pop("world", World.load(SAMPLE) if mode == "record" else None)
        app = create_app(
            settings,
            store,
            mode=mode,
            world=world,
            tokenizer=kwargs.pop("tokenizer", tokenizer),
            enable_mcp=False,
            **kwargs,  # type: ignore[arg-type]
        )
        return TestClient(app)

    yield build


def call(
    client: TestClient, tool: str, args: dict[str, object], run_id: str = "run_rec", **headers: str
):  # type: ignore[no-untyped-def]
    return client.post(
        f"/tools/{tool}", json={"args": args}, headers={"X-Ratchet-Run-Id": run_id, **headers}
    )


def steps(store: Store, run_id: str) -> list[ToolCall]:
    return [s for s in store.collect_steps(run_id) if isinstance(s, ToolCall)]


# --- record mode --------------------------------------------------------------------------
def test_record_writes_cassette_with_tokenized_args(make_client: AppFactory, store: Store) -> None:
    client = make_client("record")
    args = {"to": ["alice@example.com"], "subject": "Receipts", "body": "Thanks alice@example.com"}
    res = call(client, "gmail.send", args)
    assert res.status_code == 200
    assert res.json() == {
        "ok": True,
        "result": {"message_id": "msg_out_0001", "status": "sent", "to": ["alice@example.com"]},
    }
    norm = normalize_args("gmail.send", args, tokenizer=StubTokenizer())
    key = cassette_key("gmail.send", norm)
    path = store.cassette_path("gmail.send", key)
    raw = path.read_text(encoding="utf-8")
    assert "alice@example.com" not in raw
    cassette = json.loads(raw)
    assert cassette["args_norm"] == {
        "body": "Thanks <EMAIL_1>",
        "subject": "Receipts",
        "to": ["<EMAIL_1>"],
    }
    assert cassette["side_effect"] == "external_send" and cassette["args_raw_hash"]
    assert cassette["response"]["result"]["to"] == ["<EMAIL_1>"]
    [step] = steps(store, "run_rec")
    assert (step.source, step.cassette_key, step.args_hash, step.side_effect) == (
        "live",
        key,
        key,
        "external_send",
    )
    assert step.step_id == "t001" and step.result_hash and "<EMAIL_1>" in step.result_preview
    assert step.started_at is not None


def test_record_mode_tool_errors_are_recorded_and_replayed(make_client: AppFactory) -> None:
    rec = make_client("record")
    res = call(rec, "github.close_issue", {"repo": "acme/widgets", "issue": 99})
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    rep = make_client("replay")
    again = call(rep, "github.close_issue", {"repo": "acme/widgets", "issue": 99}, run_id="run_rep")
    assert again.status_code == 404 and again.json()["error"]["code"] == "not_found"
    assert again.json()["error"]["request_id"]


def test_unknown_tool_in_record_mode_is_404(make_client: AppFactory) -> None:
    res = call(make_client("record"), "jira.create", {})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "unknown_tool"


def test_missing_run_id_is_400_with_envelope(make_client: AppFactory) -> None:
    res = make_client("record").post(
        "/tools/time.now", json={"args": {}}, headers={"X-Request-Id": "req_t"}
    )
    assert res.status_code == 400
    assert res.json() == {
        "error": {
            "code": "missing_run_id",
            "message": "X-Ratchet-Run-Id header is required",
            "request_id": "req_t",
        }
    }


@pytest.mark.parametrize("run_id", ["../x", ".hidden", "a/b", "a" * 200])
def test_bad_run_id_is_400(make_client: AppFactory, run_id: str) -> None:
    assert call(make_client("record"), "time.now", {}, run_id=run_id).status_code == 400


def test_local_tool_is_not_served(make_client: AppFactory) -> None:
    res = call(make_client("record"), "fs.read", {"path": "a.txt"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "local_tool"


def test_wire_names_are_accepted(make_client: AppFactory) -> None:
    res = call(make_client("record"), "github__list_issues", {"repo": "acme/widgets"})
    assert res.status_code == 200 and len(res.json()["result"]["issues"]) == 3


def test_frozen_time_header_and_world_default(make_client: AppFactory) -> None:
    client = make_client("record")
    default = call(client, "time.now", {}).json()["result"]["now"]
    assert default == "2026-10-14T09:00:00Z"
    frozen = call(
        client, "time.now", {}, run_id="run_t2", **{"X-Ratchet-Frozen-Time": "2026-01-02T03:04:05Z"}
    )
    assert frozen.json()["result"]["now"] == "2026-01-02T03:04:05Z"
    bad = call(client, "time.now", {}, **{"X-Ratchet-Frozen-Time": "yesterday"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_frozen_time"


def test_run_meta_frozen_time_is_used(make_client: AppFactory, store: Store) -> None:
    from ratchet.capture.runs import RunMeta, write_run_meta
    from ratchet.clock import parse

    write_run_meta(
        store,
        RunMeta(
            run_id="run_meta",
            agent_tier="super",
            seed=1,
            frozen_time=parse("2026-03-01T00:00:00+00:00"),
        ),
    )
    res = call(make_client("record"), "time.now", {}, run_id="run_meta")
    assert res.json()["result"]["date"] == "2026-03-01"


def test_record_refuses_without_tokenizer_module_or_world(settings: Settings, store: Store) -> None:
    with pytest.raises(ConfigError):
        create_app(
            settings, store, mode="record", world=None, tokenizer=lambda v: v, enable_mcp=False
        )


def test_record_mode_is_refused_in_public_mode(tmp_path: Path, store: Store) -> None:
    public = Settings(_env_file=None, ratchet_data_dir=tmp_path / "data", ratchet_public_mode=True)  # type: ignore[call-arg]
    with pytest.raises(ConfigError):
        create_app(
            public,
            store,
            mode="record",
            world=World.load(SAMPLE),
            tokenizer=lambda v: v,
            enable_mcp=False,
        )
    client = TestClient(
        create_app(public, store, mode="replay", tokenizer=lambda v: v, enable_mcp=False)
    )
    res = client.post("/mode", json={"mode": "record"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "not_public"


# --- replay mode --------------------------------------------------------------------------
def _record_session(make_client: AppFactory) -> None:
    rec = make_client("record")
    assert call(rec, "github.list_issues", {"repo": "acme/widgets"}).status_code == 200
    assert (
        call(
            rec,
            "github.comment",
            {"repo": "acme/widgets", "issue": 12, "body": "Closing as stale."},
        ).status_code
        == 200
    )
    assert call(rec, "github.close_issue", {"repo": "acme/widgets", "issue": 13}).status_code == 200
    assert call(rec, "github.list_issues", {"repo": "acme/widgets"}).status_code == 200


def test_replay_hit_returns_stored_response(make_client: AppFactory, store: Store) -> None:
    _record_session(make_client)
    rep = make_client("replay")
    res = call(rep, "github.close_issue", {"repo": "acme/widgets", "issue": 13}, run_id="run_rep")
    assert res.status_code == 200
    assert res.json()["result"] == {
        "repo": "acme/widgets",
        "issue": 13,
        "state": "closed",
        "state_reason": "completed",
    }
    [step] = steps(store, "run_rep")
    assert step.source == "cassette" and step.cassette_key == step.args_hash and not step.blocked


def test_replay_serves_repeated_calls_in_recorded_order(make_client: AppFactory) -> None:
    _record_session(make_client)
    rep = make_client("replay")
    first = call(rep, "github.list_issues", {"repo": "acme/widgets"}, run_id="run_rep")
    second = call(rep, "github.list_issues", {"repo": "acme/widgets"}, run_id="run_rep")
    third = call(rep, "github.list_issues", {"repo": "acme/widgets"}, run_id="run_rep")
    assert [i["number"] for i in first.json()["result"]["issues"]] == [11, 12, 13]
    assert [i["number"] for i in second.json()["result"]["issues"]] == [11, 12]
    assert third.json() == second.json()
    other = call(rep, "github.list_issues", {"repo": "acme/widgets"}, run_id="run_rep2")
    assert other.json() == first.json()


def test_replay_tolerant_hit_is_served_for_reads(make_client: AppFactory, store: Store) -> None:
    rec = make_client("record")
    assert call(rec, "gmail.search", {"query": "Invoice from ACME for October"}).status_code == 200
    rep = make_client("replay")
    res = call(rep, "gmail.search", {"query": "invoice from  ACME for october "}, run_id="run_rep")
    assert res.status_code == 200
    [step] = steps(store, "run_rep")
    assert step.cassette_key != step.args_hash
    assert rep.get("/log").json()["items"] == []
    assert 'ratchet_cassette_events_total{kind="tolerant_hit"} 1.0' in rep.get("/metrics").text


def test_replay_reworded_hard_side_effect_is_not_tolerant_matched(
    make_client: AppFactory, store: Store
) -> None:
    """Hard side effects match exactly or are blocked as unexpected."""
    _record_session(make_client)
    rep = make_client("replay")
    res = call(
        rep,
        "github.comment",
        {"repo": "acme/widgets", "issue": 12, "body": "closing as  stale. "},
        run_id="run_rep",
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "ratchet_blocked"
    [logged] = rep.get("/log", params={"run_id": "run_rep"}).json()["by_run"]["run_rep"]
    assert logged["tool"] == "github.comment" and logged["blocked"] is True


def test_replay_send_to_a_different_recipient_is_unexpected(
    make_client: AppFactory, store: Store, tokenizer: StubTokenizer
) -> None:
    """Same body, ``to: attacker@evil`` must never hit the cassette."""
    tool = "gmail.send"
    body = (
        "Hi Dana, the October invoice for the Berlin office is attached as a PDF. It covers the "
        "desk rental, the two monitors and the coffee subscription. Payment is due within thirty "
        "days of receipt. Thanks, Sam"
    )
    recorded = {"to": ["dana@example.com"], "subject": "Invoice", "body": body}
    norm = normalize_args(tool, recorded, tokenizer=tokenizer)
    store.save_cassette(
        cassette_key(tool, norm),
        Cassette(
            tool=tool,
            args_norm=norm,
            response={"result": {"message_id": "m1"}},
            recorded_at=datetime(2026, 10, 14, tzinfo=UTC),
            side_effect="external_send",
        ),
    )
    rep = make_client("replay")
    assert call(rep, tool, recorded, run_id="run_ok").status_code == 200
    evil = {**recorded, "to": ["attacker@evil.example"]}
    res = call(rep, tool, evil, run_id="run_rep")
    assert res.status_code == 409 and res.json()["error"]["code"] == "ratchet_blocked"
    [logged] = rep.get("/log", params={"run_id": "run_rep"}).json()["by_run"]["run_rep"]
    assert logged["tool"] == tool and logged["side_effect"] == "external_send"
    assert logged["blocked"] is True


def test_replay_read_miss_is_synthetic_and_not_unexpected(make_client: AppFactory) -> None:
    rep = make_client("replay")
    res = call(rep, "github.list_issues", {"repo": "acme/gadgets"}, run_id="run_rep")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "result": {"repo": "acme/gadgets", "issues": []}}
    assert rep.get("/log").json()["items"] == []
    assert 'kind="miss_read"} 1.0' in rep.get("/metrics").text


def test_replay_hard_class_miss_is_blocked_and_logged(
    make_client: AppFactory, store: Store
) -> None:
    _record_session(make_client)
    rep = make_client("replay")
    res = call(rep, "github.close_issue", {"repo": "acme/widgets", "issue": 11}, run_id="run_rep")
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "ratchet_blocked" and error["request_id"]
    log = rep.get("/log", params={"run_id": "run_rep"}).json()
    [blocked] = log["by_run"]["run_rep"]
    assert blocked["tool"] == "github.close_issue" and blocked["blocked"] is True
    assert blocked["side_effect"] == "write_irreversible" and blocked["args_norm"] == {
        "issue": 11,
        "repo": "acme/widgets",
    }
    [step] = steps(store, "run_rep")
    assert step.blocked and step.result_hash is None
    persisted = (store.trace_dir("run_rep") / "unexpected.jsonl").read_text().splitlines()
    assert ToolCall.model_validate_json(persisted[0]).blocked
    assert 'kind="blocked"} 1.0' in rep.get("/metrics").text


@pytest.mark.parametrize(
    "tool,args",
    [
        ("gmail.send", {"to": ["x@example.com"], "subject": "s", "body": "b"}),
        ("payments.pay", {"invoice": "INV-1001", "amount": "250.00"}),
        ("slack.post", {"channel": "#ops", "text": "hi"}),
    ],
)
def test_every_hard_class_miss_is_blocked(
    make_client: AppFactory, tool: str, args: dict[str, object]
) -> None:
    assert call(make_client("replay"), tool, args, run_id="run_rep").status_code == 409


def test_replay_reversible_write_miss_is_synthetic_but_unexpected(make_client: AppFactory) -> None:
    rep = make_client("replay")
    res = call(rep, "sheets.append", {"sheet": "Expenses", "rows": [["a"]]}, run_id="run_rep")
    assert res.status_code == 200 and res.json()["result"] == {"sheet": "Expenses", "appended": 0}
    [item] = rep.get("/log").json()["items"]
    assert item["tool"] == "sheets.append" and item["blocked"] is False


def test_replay_unknown_tool_is_treated_as_a_hard_write(make_client: AppFactory) -> None:
    rep = make_client("replay")
    res = call(rep, "jira.create", {"title": "x"}, run_id="run_rep")
    assert res.status_code == 409
    assert rep.get("/log").json()["items"][0]["side_effect"] == "write_irreversible"


def test_overlay_cassettes_win_for_world_dependent_reads(
    make_client: AppFactory, store: Store, tmp_path: Path
) -> None:
    rec = make_client("record")
    args = {"base": "EUR", "quote": "USD", "date": "2026-10-14"}
    assert call(rec, "finance.rate", args).json()["result"]["rate"] == 1.0842
    norm = normalize_args("finance.rate", args)
    key = cassette_key("finance.rate", norm)
    cassette = store.load_cassette("finance.rate", key)
    assert cassette is not None
    overlay = tmp_path / "world_moved"
    store.save_cassette(
        key, cassette.model_copy(update={"response": {"result": {**args, "rate": 1.1203}}}), overlay
    )
    moved = make_client("replay", overlay_root=overlay)
    assert call(moved, "finance.rate", args, run_id="run_d1").json()["result"]["rate"] == 1.1203
    plain = make_client("replay")
    assert call(plain, "finance.rate", args, run_id="run_d1b").json()["result"]["rate"] == 1.0842


# --- corrupted cassette ----------------------------------------------------------------------
def test_corrupted_cassette_is_a_miss_not_a_crash(make_client: AppFactory, store: Store) -> None:
    for tool, args in (
        ("github.list_issues", {"repo": "acme/widgets"}),
        ("github.close_issue", {"repo": "acme/widgets", "issue": 13}),
    ):
        path = store.cassette_path(tool, cassette_key(tool, normalize_args(tool, args)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"tool": truncated', encoding="utf-8")
    rep = make_client("replay")
    read = call(rep, "github.list_issues", {"repo": "acme/widgets"}, run_id="run_c5")
    assert read.status_code == 200 and read.json()["result"]["issues"] == []
    hard = call(rep, "github.close_issue", {"repo": "acme/widgets", "issue": 13}, run_id="run_c5")
    assert hard.status_code == 409


# --- notify, mode, health, metrics -----------------------------------------------------------
def test_notify_records_local_tool_calls(make_client: AppFactory, store: Store) -> None:
    client = make_client("replay")
    res = client.post(
        "/tools/fs.read/notify",
        json={"args": {"path": "notes.md"}, "result": "mail bob@example.com"},
        headers=RUN,
    )
    assert res.status_code == 200 and res.json() == {"ok": True, "step_id": "t001"}
    [step] = steps(store, "run_rec")
    assert step.source == "local" and step.cassette_key is None and step.side_effect == "read"
    assert "bob@example.com" not in step.result_preview
    shell = client.post("/tools/shell__run/notify", json={"args": {"cmd": "ls"}}, headers=RUN)
    assert shell.status_code == 200
    write = client.post(
        "/tools/fs.write/notify", json={"args": {"path": "a", "content": "b"}}, headers=RUN
    )
    assert write.status_code == 400 and write.json()["error"]["code"] == "reconciled_by_diff"
    remote = client.post("/tools/gmail.send/notify", json={"args": {}}, headers=RUN)
    assert remote.status_code == 400 and remote.json()["error"]["code"] == "not_local"


def test_mode_switch_and_health(make_client: AppFactory) -> None:
    client = make_client("record")
    assert client.get("/healthz").json() == {
        "ok": True,
        "mode": "record",
        "world": "sample",
        "mcp": False,
    }
    assert client.post("/mode", json={"mode": "replay"}).json() == {"mode": "replay"}
    assert client.get("/mode").json() == {"mode": "replay"}
    assert client.post("/mode", json={"mode": "live"}).status_code == 400
    replay_only = make_client("replay")
    res = replay_only.post("/mode", json={"mode": "record"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "world_missing"


def test_bad_bodies_are_400(make_client: AppFactory) -> None:
    client = make_client("record")
    assert client.post("/tools/time.now", content=b"nope", headers=RUN).status_code == 400
    assert client.post("/tools/time.now", json=[1], headers=RUN).status_code == 400
    assert client.post("/tools/time.now", json={"args": [1]}, headers=RUN).status_code == 400


# --- no raw PII in cassettes -----------------------------------------------------------------
def test_no_raw_pii_in_cassettes_with_the_real_tokenizer(
    make_client: AppFactory, store: Store, tmp_path: Path
) -> None:
    from ratchet.pii import PiiTokenizer

    pii = PiiTokenizer(tmp_path / "pii.map.enc", tmp_path / "pii.key")
    client = make_client("record", tokenizer=pii.tokenize_obj)
    calls: list[tuple[str, dict[str, object]]] = [
        ("gmail.search", {"query": "from:alice@example.com"}),
        ("gmail.search", {"query": "planning"}),
        (
            "gmail.send",
            {
                "to": ["bob@example.com"],
                "cc": ["carol@example.com"],
                "subject": "Call +1 415 555 0199",
                "body": "Ping alice@example.com",
            },
        ),
        ("receipts.parse", {"file": "hotel-2026-10-02.pdf"}),
        (
            "calendar.freebusy",
            {
                "attendees": ["alice@example.com", "bob@example.com"],
                "start": "2026-10-15T08:00:00Z",
                "end": "2026-10-15T18:00:00Z",
            },
        ),
        (
            "calendar.create",
            {
                "title": "Sync",
                "start": "2026-10-16T10:00:00Z",
                "end": "2026-10-16T10:30:00Z",
                "attendees": ["bob@example.com"],
            },
        ),
        ("github.list_issues", {"repo": "acme/widgets", "state": "all"}),
    ]
    for tool, args in calls:
        assert call(client, tool, args).status_code == 200
    files = list(store.cassettes_dir.rglob("*.json"))
    assert len(files) == len(calls)
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not EMAIL.search(text), path
        assert "555 01" not in text, path  # the fixture's phone numbers


def test_default_tokenizer_uses_ratchet_pii_and_persists_the_map(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, ratchet_data_dir=tmp_path / "data", ratchet_pii_key_file=tmp_path / "k"
    )
    store = Store.open(settings.ratchet_data_dir)
    app = create_app(settings, store, mode="record", world=World.load(SAMPLE), enable_mcp=False)
    client = TestClient(app)
    res = call(client, "gmail.search", {"query": "from:alice@example.com"})
    assert res.status_code == 200
    assert res.json()["result"]["messages"][0]["from"] == "alice@example.com"  # live answer
    [path] = list(store.cassettes_dir.rglob("*.json"))
    assert not EMAIL.search(path.read_text(encoding="utf-8"))
    assert (settings.ratchet_data_dir / "pii.map.enc").exists()
