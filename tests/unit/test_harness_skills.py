"""Skill loading (agentskills.io rules) and the lexical trigger matcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.replay.harness.skills import (
    LEXICAL_THRESHOLD,
    Skill,
    lexical_matcher,
    load_skills,
    parse_skill,
    select_skills,
)

SKILLS = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def _write(root: Path, dirname: str, frontmatter: str) -> Path:
    (root / dirname).mkdir(parents=True)
    (root / dirname / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody text\n")
    return root / dirname


def test_load_skills_keeps_valid_and_skips_invalid():
    names = [s.name for s in load_skills(SKILLS)]
    assert names == ["expense-report", "release-notes", "schedule-meeting", "triage-issues"]


def test_parse_skill_strips_frontmatter_and_keeps_metadata_skills():
    skill = parse_skill(SKILLS / "release-notes")
    assert skill is not None
    assert skill.description.startswith("Draft release notes")
    assert skill.body.startswith("# Release notes")


@pytest.mark.parametrize(
    ("dirname", "frontmatter"),
    [
        ("bad--name", "name: bad--name\ndescription: x"),
        ("-lead", "name: -lead\ndescription: x"),
        ("other", "name: mismatch\ndescription: x"),
        ("nodesc", "name: nodesc"),
        ("empty-desc", "name: empty-desc\ndescription: '   '"),
        ("long-desc", "name: long-desc\ndescription: " + "x" * 1025),
        ("notmap", "- a\n- b"),
        ("badyaml", "name: [unclosed"),
    ],
)
def test_parse_skill_rejects_spec_violations(tmp_path, dirname, frontmatter):
    assert parse_skill(_write(tmp_path, dirname, frontmatter)) is None


def test_lexical_matcher_scores_relevant_above_threshold_and_unrelated_below():
    skills = load_skills(SKILLS)
    scores = dict(lexical_matcher("Triage the open issues in acme/widgets", skills))
    assert scores["triage-issues"] >= LEXICAL_THRESHOLD
    assert all(v < LEXICAL_THRESHOLD for k, v in scores.items() if k != "triage-issues")
    off_topic = dict(lexical_matcher("What is the weather in Paris?", skills))
    assert max(off_topic.values()) < LEXICAL_THRESHOLD


def test_select_skills_caps_at_three_best_first_and_uses_injected_matcher():
    skills = [Skill(n, "d", "b", Path(n)) for n in ("a", "b", "c", "d")]

    def matcher(task: str, pool: object) -> list[tuple[str, float]]:
        return [("a", 0.7), ("b", 0.9), ("c", 0.7), ("d", 0.95), ("ghost", 1.0)]

    chosen = select_skills("t", skills, matcher=matcher, threshold=0.6)
    assert [s.name for s in chosen] == ["d", "b", "a"]
    assert select_skills("t", skills, matcher=matcher, threshold=0.99) == []
