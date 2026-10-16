"""Tolerant matching and synthetic miss responses."""

from __future__ import annotations

from datetime import UTC, datetime

from ratchet.mocktools.matching import best_tolerant, fingerprint, jaccard, tolerant_score
from ratchet.mocktools.synthetic import parse_templates, synthetic_result
from ratchet.mocktools.world import CallContext

LONG = " ".join(f"word{i}" for i in range(30))
CTX = CallContext(run_id="run_x", now=datetime(2026, 10, 14, 9, 0, tzinfo=UTC))


def test_whitespace_and_case_differences_score_one() -> None:
    a = fingerprint({"repo": "acme/widgets", "body": "Closing as  STALE"})
    b = fingerprint({"repo": "acme/widgets", "body": "closing as stale "})
    assert tolerant_score(a, b) == 1.0


def test_one_changed_word_in_a_long_body_still_matches() -> None:
    a = fingerprint({"issue": 12, "body": LONG})
    b = fingerprint({"issue": 12, "body": LONG.replace("word7", "other")})
    score = tolerant_score(a, b)
    assert score is not None and score >= 0.9


def test_different_non_string_values_never_match() -> None:
    a = fingerprint({"issue": 12, "body": LONG})
    b = fingerprint({"issue": 13, "body": LONG})
    assert tolerant_score(a, b) is None


def test_different_key_sets_never_match() -> None:
    a = fingerprint({"repo": "acme/widgets"})
    b = fingerprint({"repo": "acme/widgets", "state": "open"})
    assert tolerant_score(a, b) is None


def test_short_value_changes_fall_below_threshold() -> None:
    call = fingerprint({"labels": ["bug"], "repo": "acme/widgets"})
    other = fingerprint({"labels": ["stale"], "repo": "acme/widgets"})
    assert best_tolerant(call, [("k" * 64, other)]) is None


def test_best_tolerant_prefers_highest_score() -> None:
    call = fingerprint({"body": LONG})
    near = fingerprint({"body": LONG + " extra"})
    exact = fingerprint({"body": LONG.upper()})
    assert best_tolerant(call, [("b" * 64, near), ("a" * 64, exact)]) == ("a" * 64, 1.0)


def test_jaccard_of_empty_sets_is_one() -> None:
    assert jaccard(frozenset(), frozenset()) == 1.0


def test_synthetic_read_template_renders_args() -> None:
    result = synthetic_result("github.list_issues", {"repo": "acme/widgets"}, "read", CTX)
    assert result == {"repo": "acme/widgets", "issues": []}


def test_synthetic_defaults_by_side_effect() -> None:
    templates = parse_templates({"default_read": {"empty": True}, "tools": {}})
    assert synthetic_result("x.read", {}, "read", CTX, templates) == {"empty": True}
    assert synthetic_result("x.write", {}, "write_reversible", CTX, templates) == {"status": "ok"}


def test_synthetic_time_now_uses_the_frozen_clock() -> None:
    assert synthetic_result("time.now", {}, "read", CTX)["now"] == "2026-10-14T09:00:00Z"
