"""Microcopy: templates, labels, errors, stages, notifications."""

from __future__ import annotations

import pytest

from ratchet import copy


def test_decision_headlines_reproduce_the_copy_examples() -> None:
    assert copy.headline_block_level0("github-triage", "close issues", 6, 14) == (
        "Blocked: the new github-triage would close issues in 6 of 14 replays."
    )
    assert copy.headline_block_cascade("expense-report", "drops the VAT line", 3, 10) == (
        "Blocked: expense-report now drops the VAT line on 3 of 10 past tasks."
    )
    assert copy.headline_warn("judges disagreed", 1, 12) == (
        "Held: judges disagreed on 1 of 12 replays. Review before merging."
    )
    assert copy.headline_pass("changelog-writer", 9) == (
        "Passed: changelog-writer still handles all 9 past tasks."
    )
    assert copy.headline_pass_drift(10, 4) == (
        "Passed: 10 tasks checked; 4 changed because the world did, not the skill."
    )
    assert (
        copy.headline_no_traces("x")
        == "Passed: no blessed tasks use x yet. Bless one to protect it."
    )


def test_outcome_labels_and_explanations() -> None:
    assert copy.OUTCOME_LABELS == {
        "pass": "Same result", "regression": "Got worse", "drift": "World changed",
        "improved": "Better", "uncertain": "Not sure", "failed": "Could not replay",
    }  # fmt: skip
    assert copy.explain_pass() == "Output meets the criteria; differences are cosmetic."
    assert copy.explain_regression("VAT line present.") == "VAT line present is no longer met."
    assert copy.explain_drift("EUR/USD rate") == (
        "EUR/USD rate changed since the original run; the new output follows from it."
    )
    assert copy.explain_improved("clarity") == "Meets the criteria and improves clarity."
    assert copy.explain_uncertain("Judges disagreed") == "Judges disagreed. Held for a human."
    assert copy.explain_failed("sandbox_failed") == "Sandbox error on this task."


def test_tier_and_judge_copy() -> None:
    assert copy.TIER_PRODUCT_NAMES["nano"] == "Nemotron Nano"
    assert copy.badge_tooltip("super", "regressed", 0.884, "VAT dropped.") == (
        "Super judged regressed with 88% confidence. VAT dropped."
    )
    assert copy.SKIPPED_TIER == "Not needed: decided earlier in the cascade."
    assert copy.level0_marker(2, "external_send") == (
        "Checked without a model: 2 new external_send call(s)."
    )
    assert copy.TRUNCATED_MARKER == "Some evidence was shortened to fit this judge's context."
    assert copy.cost_with_tier(0.04, "nano") == "$0.04 (nano)"
    assert copy.cost_with_tier(0.0012, "nano") == "$0.0012 (nano)"


def test_progress_stage_labels() -> None:
    labels = [
        copy.stage_label("detected"),
        copy.stage_label("selecting", n=12),
        copy.stage_label("replaying", 9, 14),
        copy.stage_label("level0"),
        copy.stage_label("nano", n=14),
        copy.stage_label("super", n=4),
        copy.stage_label("ultra", n=1),
        copy.stage_label("drift", n=2),
        copy.stage_label("narrative"),
        copy.stage_label("done"),
    ]
    assert " → ".join(labels) == (
        "Detected edit → Choosing tasks (12) → Replaying on Nebius Jobs (9/14) → Checking actions"
        " → Nano screening (14) → Super checking (4) → Ultra deciding (1) → Asking Tavily (2 facts)"
        " → Writing the summary → Done"
    )
    assert copy.stage_label("drift", n=1) == "Asking Tavily (1 fact)"
    assert (
        copy.stage_label("replaying", runner="the local runner") == "Replaying on the local runner"
    )
    assert copy.stalled_label("Ultra deciding") == (
        "Still working — Ultra deciding is taking longer than usual."
    )
    assert len(copy.STAGE_KEYS) == 11
    with pytest.raises(ValueError):
        copy.stage_label("bogus")


def test_actions_and_confirmations() -> None:
    assert copy.rollback_confirm("abc123").startswith("This reverts commit abc123 on candidate")
    assert copy.ROLLBACK_TOAST == "Rolled back. main is unchanged."
    assert "new expense-report on its next task" in copy.approve_confirm("expense-report")
    assert copy.approve_toast("expense-report") == "Merged. expense-report is live."
    assert copy.rebless_confirm("Convert hotel") == (
        'This makes the replay the new reference for "Convert hotel" and archives the old one '
        'with the reason "world changed".'
    )
    assert copy.REBLESS_TOAST == "Re-blessed. Future checks compare against today's result."


def test_error_copy_by_code() -> None:
    assert copy.error_copy("replay_infra", n=3, m=10).startswith("Replays failed on 3 of 10 tasks")
    assert copy.error_copy("replay_infra").startswith("Replays failed on tasks")
    assert copy.error_copy("judge_timeout") == (
        "The final judge timed out. The edit is held for you to review."
    )
    assert copy.error_copy("schema_failure") == "Judge answer unreadable; treated as uncertain."
    assert copy.reason_text("drift_unavailable") == "Could not verify whether the world changed."
    assert "not sure" in copy.reason_text("something_new")
    for code in ("upstream_unavailable", "drift_unavailable", "budget_exceeded", "merge_conflict",
                 "not_public"):  # fmt: skip
        assert code in copy.ERROR_COPY and code in copy.REASON_TEXT


def test_empty_states_and_closings() -> None:
    assert copy.EMPTY_TIMELINE.startswith("Nothing here yet.")
    assert copy.EMPTY_VERDICTS.startswith("No skill edits checked yet.")
    assert copy.EMPTY_TRACES == "No blessed tasks. Run a task, then press Bless on it."
    assert copy.EMPTY_COSTS == "No spend yet."
    assert copy.CLOSING_SENTENCES == (
        "Nothing to do.", "Approve if this is intended.", "Roll back if this is not intended.",
        "Re-bless the changed tasks.",
    )  # fmt: skip


def test_notification_is_at_most_160_chars_and_ellipsizes_the_headline() -> None:
    short = copy.notification("BLOCK", "Blocked: x.", "https://r.example/v/1")
    assert short == "Ratchet BLOCK: Blocked: x. — https://r.example/v/1"
    long = copy.notification("WARN", "Held: " + "word " * 60, "https://r.example/verdicts/v_123")
    assert len(long) <= 160 and "…" in long and long.endswith("https://r.example/verdicts/v_123")
    silly = copy.notification("PASS", "x", "https://" + "a" * 200)
    assert len(silly) <= 160


def test_voice_rules_no_exclamation_marks_anywhere() -> None:
    strings = [v for v in vars(copy).values() if isinstance(v, str)]
    strings += [
        s for d in (copy.ERROR_COPY, copy.REASON_TEXT, copy.OUTCOME_LABELS) for s in d.values()
    ]
    assert strings and not any("!" in s for s in strings)


@pytest.mark.parametrize(
    ("criterion", "phrase"),
    [
        ("The output includes the VAT line", "drops the VAT line"),
        ("Output includes a due date.", "drops a due date"),
        ("The report lists every receipt total", "no longer includes every receipt total"),
        ("sheets.append is called", "no longer calls sheets.append"),
        (
            "states the VAT amount '3.80 EUR' for the second receipt",
            "omits the VAT amount '3.80 EUR' for the second receipt",
        ),
        ("mentions that the update is late", 'fails "mentions that the update is late"'),
        (
            "Every open issue gets exactly one label.",
            'fails "Every open issue gets exactly one label"',
        ),
    ],
)
def test_failure_phrase_reads_like_the_copy_examples(criterion: str, phrase: str) -> None:
    assert copy.failure_phrase(criterion) == phrase


def test_cascade_headline_with_vat_matches_the_copy_example() -> None:
    headline = copy.headline_block_cascade(
        "expense-report", copy.failure_phrase("The output includes the VAT line"), 3, 10
    )
    assert headline == "Blocked: expense-report now drops the VAT line on 3 of 10 past tasks."


def test_every_user_visible_refusal_code_has_copy() -> None:
    """Codes the dashboard can show (toasts, banners, badges) all have copy;
    ``dashboard/src/copy.ts`` mirrors them."""
    visible = (
        "rate_limited", "worker_lost", "simulate_disabled", "simulate_busy",
        "action_token_invalid", "already_reverted", "not_ready", "stacked_on_held",
        "payload_too_large",
    )  # fmt: skip
    for code in visible:
        assert code in copy.ERROR_COPY and code in copy.REASON_TEXT, code
        assert copy.error_copy(code) == copy.ERROR_COPY[code]
        assert len(copy.REASON_TEXT[code].split()) < 20


def test_rollback_confirm_has_a_merged_variant() -> None:
    from ratchet.copy import confirm_rollback_merged, rollback_confirm

    assert confirm_rollback_merged("abc1234") == (
        "This reverts commit abc1234 on candidate and removes the change from main. "
        "You can re-apply it later with Approve anyway."
    )
    assert "leaves main unchanged" in rollback_confirm("abc1234")


def test_action_toast_follows_the_prior_action() -> None:
    from ratchet.copy import action_toast

    assert action_toast("rollback", "merged", ["x"]) == "Rolled back. main no longer has this edit."
    assert action_toast("rollback", "held", ["x"]) == "Rolled back. main is unchanged."
    assert action_toast("approve", "held", ["a", "b"]) == "Merged. a, b is live."
    assert action_toast("approve", "reverted", []).startswith("Approved anyway. the skill")
