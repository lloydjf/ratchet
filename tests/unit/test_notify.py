"""Notifications: payload, 160-char message, retries, never raises."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from ratchet.config.settings import Settings
from ratchet.models import SkillEdit, Verdict
from ratchet.orchestrator.notify import (
    MESSAGE_LIMIT,
    build_payload,
    first_sentence,
    format_message,
    notify,
)

HOOK = "https://hooks.example.test/services/T000/B000/secret"
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def verdict(**update: object) -> Verdict:
    base = Verdict(
        verdict_id="verdict_abc123",
        edit_id="edit_1",
        pairs=[],
        decision="BLOCK",
        action="reverted",
        narrative=(
            "Blocked: the new github-triage would close issues in 6 of 14 replays. "
            "Roll back if this is not intended."
        ),
        cost={"nano": 0.01, "super": 0.02, "ultra": 0.1},
        duration_s=40,
        created_at=NOW,
        headline="Blocked: the new github-triage would close issues in 6 of 14 replays.",
    )
    return base.model_copy(update=update)


def edit() -> SkillEdit:
    return SkillEdit(
        edit_id="edit_1",
        commit="c" * 40,
        parent="p" * 40,
        files=["github-triage/SKILL.md"],
        diff_ref="edits/edit_1/diff.patch",
        affected_skills=["github-triage"],
        author="agent",
        detected_at=NOW,
        summary="github-triage: SKILL.md changed (+3 −1)",
    )


def settings(tmp_path: Path, webhook: str | None = HOOK) -> Settings:
    return Settings(ratchet_notify_webhook=webhook, ratchet_data_dir=tmp_path)


def test_message_format_fits_160_and_keeps_the_link() -> None:
    url = "https://ratchet.example/verdicts/verdict_abc123"
    short = format_message("PASS", "Passed: changelog-writer still handles all 9 past tasks.", url)
    assert short == (
        "Ratchet PASS: Passed: changelog-writer still handles all 9 past tasks. — " + url
    )
    long = format_message("BLOCK", "Blocked: " + "very " * 60 + "bad.", url)
    assert MESSAGE_LIMIT - 1 <= len(long) <= MESSAGE_LIMIT
    assert long.endswith("… — " + url)
    assert long.startswith("Ratchet BLOCK: Blocked: very")


def test_message_drops_the_headline_when_the_url_leaves_no_room() -> None:
    url = "https://x.test/" + "a" * 150
    message = format_message("WARN", "Held: judges disagreed.", url)
    assert len(message) <= MESSAGE_LIMIT
    assert message.startswith("Ratchet WARN — https://x.test/")


def test_first_sentence() -> None:
    assert first_sentence("Held: x on 1 of 2.  Review before merging.") == "Held: x on 1 of 2."
    assert first_sentence("") == ""


def test_payload_matches_the_contract() -> None:
    payload = build_payload(verdict(), edit(), "https://ratchet.example/")
    assert payload["verdict_id"] == "verdict_abc123"
    assert payload["decision"] == "BLOCK" and payload["action"] == "reverted"
    assert payload["edit"] == {
        "edit_id": "edit_1",
        "affected_skills": ["github-triage"],
        "author": "agent",
    }
    assert payload["summary"] == (
        "Blocked: the new github-triage would close issues in 6 of 14 replays."
    )
    assert payload["cost_usd"] == pytest.approx(0.13)
    assert payload["url"] == "https://ratchet.example/verdicts/verdict_abc123"
    text = str(payload["text"])
    assert text.startswith("Ratchet BLOCK: Blocked: the new github-triage")
    assert len(text) <= MESSAGE_LIMIT
    assert payload["simulated_models"] is False


def test_payload_headline_falls_back_to_narrative_then_edit_summary() -> None:
    from_narrative = build_payload(verdict(headline=""), edit(), "http://h")
    assert from_narrative["headline"] == from_narrative["summary"]
    empty = build_payload(verdict(headline="", narrative=""), edit(), "http://h")
    assert empty["headline"] == "github-triage: SKILL.md changed (+3 −1)"


@respx.mock
async def test_notify_posts_json_with_slack_text(tmp_path: Path) -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))
    payload = build_payload(verdict(), edit(), "http://h")
    assert await notify(payload, settings(tmp_path), backoff_s=0) is True
    body = json.loads(route.calls.last.request.content)
    assert body["text"] == payload["text"]
    assert body["verdict_id"] == "verdict_abc123"


@respx.mock
async def test_notify_retries_twice_then_gives_up(tmp_path: Path) -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(503))
    assert await notify({"verdict_id": "v"}, settings(tmp_path), backoff_s=0) is False
    assert route.call_count == 3


@respx.mock
async def test_notify_recovers_on_retry(tmp_path: Path) -> None:
    route = respx.post(HOOK).mock(
        side_effect=[httpx.ConnectTimeout("slow"), httpx.Response(429), httpx.Response(204)]
    )
    assert await notify({"verdict_id": "v"}, settings(tmp_path), backoff_s=0) is True
    assert route.call_count == 3


@respx.mock
async def test_notify_does_not_retry_client_errors(tmp_path: Path) -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(404))
    assert await notify({"verdict_id": "v"}, settings(tmp_path), backoff_s=0) is False
    assert route.call_count == 1


async def test_notify_without_webhook_is_a_noop(tmp_path: Path) -> None:
    assert await notify({"verdict_id": "v"}, settings(tmp_path, webhook=None)) is False


async def test_notify_never_raises(tmp_path: Path) -> None:
    class Exploding(httpx.AsyncClient):
        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise RuntimeError("boom")

    async with Exploding() as client:
        result = await notify({"verdict_id": "v"}, settings(tmp_path), client=client)
    assert result is False


async def test_notify_uses_a_five_second_timeout(tmp_path: Path) -> None:
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout"))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await notify({"verdict_id": "v"}, settings(tmp_path), client=client)
    assert seen == [{"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}]


def test_notify_webhook_is_a_secret_setting(tmp_path: Path) -> None:
    """The webhook URL carries its own credential; it never shows in repr/dumps."""
    s = settings(tmp_path)
    assert s.ratchet_notify_webhook is not None
    assert HOOK not in repr(s) and HOOK not in s.model_dump_json()
    assert s.ratchet_notify_webhook.get_secret_value() == HOOK
