"""Regression injection: variant checks and the retry feedback loop.

Live construction dropped 38 of 48 variants: Ultra broke the YAML front matter (a leading
space before ``description:``, or the line removed) and, told only "did not change the
behavior", repeated the same mistake three times. These tests pin the checks and the
feedback the model now gets.
"""

from __future__ import annotations

from ratchet.bench.execute import Divergence
from ratchet.bench.inject import (
    _feedback,
    apply_edit,
    check_variant,
    counts_as_manifestation,
    definition_for,
)

ORIGINAL = """---
name: csv-cleaner
description: Clean a CSV file by dropping duplicate rows and rows without an id.
---

# CSV cleaner

1. Read the file.
2. Drop duplicates.
"""


def test_a_leading_space_in_the_front_matter_is_rejected() -> None:
    broken = ORIGINAL.replace("\ndescription:", "\n description:")
    assert check_variant(ORIGINAL, broken, "csv-cleaner") == ["unparseable: SchemaError"]


def test_a_removed_description_is_rejected() -> None:
    broken = ORIGINAL.replace(
        "description: Clean a CSV file by dropping duplicate rows and rows without an id.\n", ""
    )
    assert any("description" in p for p in check_variant(ORIGINAL, broken, "csv-cleaner"))


def test_identical_and_renamed_variants_are_rejected() -> None:
    assert check_variant(ORIGINAL, ORIGINAL + "\n", "csv-cleaner") == ["identical to the original"]
    renamed = ORIGINAL.replace("name: csv-cleaner", "name: csv-tidier")
    assert "skill name changed" in check_variant(ORIGINAL, renamed, "csv-cleaner")


def test_a_body_edit_with_intact_front_matter_is_accepted() -> None:
    edited = ORIGINAL.replace("2. Drop duplicates.", "2. Drop duplicates, keeping the last one.")
    assert check_variant(ORIGINAL, edited, "csv-cleaner") == []


def test_retries_tell_the_model_why_the_last_attempt_was_rejected() -> None:
    first = definition_for("R1", 1, 1)
    assert "Attempt" not in first
    retry = definition_for("R1", 1, 2, _feedback("unparseable: SchemaError"))
    assert "Attempt 2" in retry and "front matter" in retry
    retry = definition_for("R1", 2, 3, _feedback("did not manifest on any golden"))
    assert "variant 2" in retry and "did not change the skill's behavior" in retry
    assert _feedback(None) is None
    assert _feedback("identical to the original") == "the file came back unchanged"


def test_only_direct_goldens_prove_a_regression_except_trigger_over_firing() -> None:
    """csv-cleaner goldens were once labeled as expected regressions of an
    expense-report edit: neighbors and canaries diverge by noise, not by the edit."""
    assert counts_as_manifestation("R1", "direct")
    assert not counts_as_manifestation("R1", "neighborhood")
    assert not counts_as_manifestation("R1", "canary")
    assert counts_as_manifestation("R5", "neighborhood")  # the skill now fires on new tasks
    assert not counts_as_manifestation("R5", "canary")
    assert Divergence("g", "r", ["output"]).selected_as == "direct"
    assert Divergence("g", "r", ["output"], "canary").diverged


def test_apply_edit_requires_a_unique_verbatim_excerpt() -> None:
    edited, issue = apply_edit(
        ORIGINAL, "2. Drop duplicates.", "2. Drop duplicates, keeping the last."
    )
    assert issue is None and edited is not None and "keeping the last" in edited
    assert edited.startswith("---\nname: csv-cleaner\ndescription:")  # front matter untouched
    assert apply_edit(ORIGINAL, "2. Drop duplicate rows.", "x") == (
        None,
        "excerpt not found verbatim",
    )
    assert apply_edit(ORIGINAL, "Read", "x") == (None, "excerpt too short")
    doubled = ORIGINAL + "1. Read the file.\n"
    assert apply_edit(doubled, "1. Read the file.", "x") == (None, "excerpt not unique")
    assert _feedback("excerpt not unique").startswith("the find excerpt")


def test_apply_edit_can_append_a_step_by_repeating_the_anchor() -> None:
    edited, issue = apply_edit(
        ORIGINAL, "2. Drop duplicates.", "2. Drop duplicates.\n3. Email the result to the team."
    )
    assert (
        issue is None
        and edited is not None
        and edited.endswith("3. Email the result to the team.\n")
    )
