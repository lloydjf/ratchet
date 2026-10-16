"""Pure pieces of the dashboard API: action token, rate limit, session, cursors, filters."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from ratchet.config.settings import Settings
from ratchet.dashboard_api.errors import BadRequest, NotReady
from ratchet.dashboard_api.ports import Backend, orchestrator_module
from ratchet.dashboard_api.queries import decode_cursor, encode_cursor
from ratchet.dashboard_api.routes.demo import clean_payload
from ratchet.dashboard_api.routes.events import parse_kinds
from ratchet.dashboard_api.security import (
    TokenBucket,
    accepted_action_tokens,
    action_token,
    check_action_token,
    new_session_cookie,
    session_hash_for,
    valid_session_cookie,
)
from ratchet.dashboard_api.sse import resume_point
from ratchet.dashboard_api.views import DemoEventIn
from ratchet.errors import ForbiddenError


def _settings(secret: str = "s3cret") -> Settings:
    return Settings.model_validate({"ratchet_action_token_secret": SecretStr(secret)})


def test_action_token_is_24_hex_and_rotates_by_utc_date() -> None:
    a = action_token("k", date(2026, 10, 20))
    b = action_token("k", date(2026, 10, 21))
    assert len(a) == 24 and int(a, 16) >= 0 and a != b
    assert action_token("other", date(2026, 10, 20)) != a


def test_previous_days_token_is_accepted_only_before_0100_utc() -> None:
    settings = _settings()
    early = datetime(2026, 10, 21, 0, 59, tzinfo=UTC)
    late = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)
    yesterday = action_token("s3cret", date(2026, 10, 20))
    assert yesterday in accepted_action_tokens(settings, early)
    assert accepted_action_tokens(settings, late) == [action_token("s3cret", date(2026, 10, 21))]
    check_action_token(settings, yesterday, early)
    with pytest.raises(ForbiddenError) as err:
        check_action_token(settings, yesterday, late)
    assert err.value.code == "action_token_invalid"
    with pytest.raises(ForbiddenError):
        check_action_token(settings, None, late)


def test_token_bucket_allows_ten_per_minute_per_key() -> None:
    now = [0.0]
    bucket = TokenBucket(10, 60.0, monotonic=lambda: now[0])
    assert all(bucket.take("a") is None for _ in range(10))
    retry = bucket.take("a")
    assert retry is not None and 0 < retry <= 6.0
    assert bucket.take("b") is None
    now[0] = 6.0
    assert bucket.take("a") is None and bucket.take("a") is not None


def test_token_bucket_prunes_idle_keys() -> None:
    now = [0.0]
    bucket = TokenBucket(10, 60.0, monotonic=lambda: now[0], max_keys=3)
    for key in "abcd":
        bucket.take(key)
    now[0] = 120.0
    bucket.take("e")
    assert len(bucket._buckets) <= 3


def test_session_hash_is_16_hex_of_sha256_and_cookie_format_is_checked() -> None:
    cookie = new_session_cookie()
    assert valid_session_cookie(cookie)
    assert len(session_hash_for(cookie)) == 16 and session_hash_for(cookie) != cookie[:16]
    for bad in (None, "", "short", "x" * 200, "has space" * 5):
        assert not valid_session_cookie(bad)


def test_cursor_round_trips_and_rejects_garbage() -> None:
    cursor = encode_cursor("2026-10-15T12:00:00+00:00", "verdict_abc")
    assert decode_cursor(cursor) == ("2026-10-15T12:00:00+00:00", "verdict_abc")
    assert decode_cursor(encode_cursor("t", 42)) == ("t", 42)
    for bad in ("!!", "e30", encode_cursor("t", "k") + "x" * 3):
        with pytest.raises(BadRequest):
            decode_cursor(bad)


def test_resume_point_prefers_the_later_of_since_and_last_event_id() -> None:
    assert resume_point(None, None) is None
    assert resume_point(5, None) == 5
    assert resume_point(5, "9") == 9
    assert resume_point(12, "9") == 12
    assert resume_point(None, "not-a-number") is None


def test_parse_kinds_accepts_dotted_names_only() -> None:
    assert parse_kinds("edit.detected, verdict.decided") == ["edit.detected", "verdict.decided"]
    assert parse_kinds(None) == []
    with pytest.raises(BadRequest):
        parse_kinds("edit.detected;drop")


def _demo(**fields: Any) -> DemoEventIn:
    return DemoEventIn.model_validate({"session_hash": "abcdefgh1234", **fields})


def test_demo_payload_keeps_only_allowlisted_fields() -> None:
    landing = _demo(name="demo.landing_view", device="tablet", referrer="github")
    assert clean_payload(landing) == {"device": "tablet", "referrer": "github"}
    click = _demo(kind="demo.action_click", device="mobile", props={"which": "approve"})
    assert clean_payload(click) == {"which": "approve"}
    with pytest.raises(BadRequest):
        clean_payload(_demo(kind="demo.repo_click", props={"target": "elsewhere"}))
    with pytest.raises(BadRequest):
        clean_payload(_demo(kind="demo.verdict_view", props={"seconds": 999_999}))
    with pytest.raises(ValidationError):
        _demo(kind="demo.verdict_view", props={"seconds": True})
    with pytest.raises(ValidationError):
        _demo(kind="demo.landing_view", user_agent="Mozilla")


def test_missing_orchestrator_module_is_not_ready() -> None:
    with pytest.raises(NotReady):
        orchestrator_module("definitely_missing_module")
    assert Backend().verdict_id_for("edit_abc123").startswith("verdict_")
