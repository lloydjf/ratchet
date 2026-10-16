"""SSE on /api/v1/events/stream: frames, resume, heartbeat, isolation,
disconnect. Streams end after ``StreamConfig.max_duration_s`` so httpx (which buffers ASGI
responses) returns quickly and deterministically."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_fakes import api_reset, make_env  # noqa: E402

from ratchet.dashboard_api.sse import StreamConfig, event_frames  # noqa: E402

__all__ = ["api_reset"]
STREAM = "/api/v1/events/stream"


def frames(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            name, _, value = line.partition(":")
            fields[name or "comment"] = value.strip()
        if fields:
            out.append(fields)
    return out


def events_of(text: str) -> list[dict[str, str]]:
    return [f for f in frames(text) if "id" in f]


async def test_stream_frames_have_id_event_and_row_json(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    first = env.store.emit("edit.detected", "edit_1", {"edit_id": "edit_1"})
    async with env.client() as client:
        res = await client.get(STREAM, params={"since": first - 1})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    (frame,) = events_of(res.text)
    assert frame["id"] == str(first) and frame["event"] == "edit.detected"
    row = json.loads(frame["data"])
    assert row["kind"] == "edit.detected" and row["payload"] == {"edit_id": "edit_1"}


async def test_stream_resumes_after_last_event_id(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    ids = [env.store.emit("batch.heartbeat", "b", {"k": i}) for i in range(5)]
    async with env.client() as client:
        resumed = await client.get(STREAM, headers={"Last-Event-ID": str(ids[2])})
        # The browser keeps the original ?since= on reconnect; Last-Event-ID wins when later.
        both = await client.get(
            STREAM, params={"since": ids[0]}, headers={"Last-Event-ID": str(ids[3])}
        )
    assert [f["id"] for f in events_of(resumed.text)] == [str(ids[3]), str(ids[4])]
    assert [f["id"] for f in events_of(both.text)] == [str(ids[4])]


async def test_stream_without_cursor_sends_only_new_events(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.store.emit("edit.detected", "old", {})

    async def emit_later() -> int:
        await asyncio.sleep(0.08)
        return await asyncio.to_thread(env.store.emit, "verdict.decided", "new", {})

    async with env.client() as client:
        task = asyncio.create_task(emit_later())
        res = await client.get(STREAM)
        new_id = await task
    assert [f["id"] for f in events_of(res.text)] == [str(new_id)]


async def test_stream_is_session_filtered_and_kind_filtered(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        await client.get("/api/v1/meta")
        start = (await client.post("/api/v1/simulate/1")).json()
        mine = start["session_hash"]
        env.store.emit("pair.decided", "p", {}, session_hash="othersession1234")
        env.store.emit("pair.decided", "p", {}, session_hash=mine)
        env.store.emit("pair.decided", "p", {})
        res = await client.get(STREAM, params={"since": 0})
        kinds = await client.get(STREAM, params={"since": 0, "kinds": "simulate.started"})
    sessions = {json.loads(f["data"])["session_hash"] for f in events_of(res.text)}
    assert sessions == {mine, None}
    assert {f["event"] for f in events_of(kinds.text)} == {"simulate.started"}


async def test_stream_sends_heartbeat_comments(tmp_path: Path) -> None:
    env = make_env(tmp_path, stream=StreamConfig(poll_s=0.01, heartbeat_s=0.05, max_duration_s=0.3))
    async with env.client() as client:
        res = await client.get(STREAM)
    comments = [f["comment"] for f in frames(res.text) if "comment" in f]
    assert comments and all(c.startswith("ping") for c in comments)


async def test_stream_stops_cleanly_when_the_client_disconnects(tmp_path: Path) -> None:
    env = make_env(tmp_path, stream=StreamConfig(poll_s=0.01, max_duration_s=None))
    sent: list[dict[str, object]] = []
    gone = asyncio.Event()

    async def receive() -> dict[str, object]:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": STREAM,
        "raw_path": STREAM.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 5000),
        "root_path": "",
    }
    task = asyncio.create_task(env.app(scope, receive, send))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    await asyncio.to_thread(env.store.emit, "edit.detected", "live", {})
    await asyncio.sleep(0.1)
    gone.set()
    await asyncio.wait_for(task, timeout=2.0)
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")  # type: ignore[misc]
    assert b"event: edit.detected" in body


async def test_event_frames_generator_closes_without_error(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.store.emit("edit.detected", "e", {})
    gen = event_frames(
        env.store,
        session_hash=None,
        after=0,
        kinds=[],
        config=StreamConfig(poll_s=0.01),
    )
    first = await gen.__anext__()
    await gen.aclose()
    assert first.event == "edit.detected"


def _scope(ip: str) -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": STREAM,
        "raw_path": STREAM.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": (ip, 5000),
        "root_path": "",
    }


class _OpenStream:
    """A long-lived raw ASGI stream (httpx buffers whole responses)."""

    def __init__(self, env: object, ip: str) -> None:
        self.gone = asyncio.Event()
        self.sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            await self.gone.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            self.sent.append(message)

        app = env.app  # type: ignore[attr-defined]
        self.task = asyncio.create_task(app(_scope(ip), receive, send))

    @property
    def body(self) -> bytes:
        parts = [m.get("body", b"") for m in self.sent if m["type"] == "http.response.body"]
        return b"".join(p for p in parts if isinstance(p, bytes))

    async def close(self) -> None:
        self.gone.set()
        await asyncio.wait_for(self.task, timeout=2.0)


async def test_streams_are_capped_per_client_and_in_total(tmp_path: Path) -> None:
    """Per-IP and global concurrent stream caps → 429 rate_limited; closing a
    stream frees its slot."""
    env = make_env(
        tmp_path,
        stream=StreamConfig(poll_s=0.01, max_duration_s=None),
        ratchet_sse_max_per_client=1,
        ratchet_sse_max_streams=2,
    )
    slots = env.app.state.services.stream_slots
    first = _OpenStream(env, "203.0.113.1")
    await asyncio.sleep(0.05)
    async with env.client("203.0.113.1") as client:
        refused = await client.get(STREAM)
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limited"
    second = _OpenStream(env, "203.0.113.2")
    await asyncio.sleep(0.05)
    async with env.client("203.0.113.3") as client:
        full = await client.get(STREAM)
    assert full.status_code == 429 and slots.open == 2
    await first.close()
    await second.close()
    assert slots.open == 0


async def test_one_shared_poller_fans_out_to_every_stream(tmp_path: Path) -> None:
    """N open streams cost one events-table read per tick, not N."""
    env = make_env(tmp_path, stream=StreamConfig(poll_s=0.01, max_duration_s=None))
    calls: list[int] = []
    real_events = env.store.events

    def counting(**kwargs: object) -> object:
        calls.append(1)
        return real_events(**kwargs)  # type: ignore[arg-type]

    env.store.events = counting  # type: ignore[method-assign]
    streams = [_OpenStream(env, f"198.51.100.{i}") for i in range(3)]
    await asyncio.sleep(0.05)
    hub = env.app.state.services.hub()
    assert hub.subscribers == 3
    await asyncio.to_thread(env.store.emit, "edit.detected", "fan", {})
    await asyncio.sleep(0.1)
    polls = hub.polls
    for stream in streams:
        await stream.close()
    assert all(b"event: edit.detected" in s.body for s in streams)
    assert polls >= 3 and len(calls) <= polls + 1  # only the shared poller (one in flight)
    assert hub.subscribers == 0
