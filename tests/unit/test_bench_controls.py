"""N0 cosmetic controls are deterministic and body-only; D1 overlays move exactly one value and
their Tavily fixtures report it."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ratchet.bench import controls
from ratchet.errors import UpstreamError
from ratchet.judge.tavily import FixtureTavily
from ratchet.models import EnvSnapshot, Trace, WorldFact
from ratchet.skills import parse_skill_md, validate_skill

BENCH = Path(__file__).resolve().parents[2] / "bench"
SKILLS = sorted(p.name for p in (BENCH / "skills").iterdir() if p.is_dir())


def _front(text: str) -> str:
    return text.split("\n---\n", 1)[0]


@pytest.mark.parametrize("skill", SKILLS)
@pytest.mark.parametrize("variant", [1, 2])
def test_n0_edits_are_deterministic_cosmetic_and_valid(skill: str, variant: int) -> None:
    original = (BENCH / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    first, summary = controls.cosmetic(original, variant)
    second, _ = controls.cosmetic(original, variant)
    assert first == second and summary
    assert first != original
    assert _front(first) == _front(original)  # name + trigger description untouched
    doc = parse_skill_md(first)
    assert validate_skill(doc, dir_name=skill) == []
    numbered = [line for line in original.splitlines() if line[:2].strip().rstrip(".").isdigit()]
    assert len(numbered) == len(
        [line for line in first.splitlines() if line[:2].strip().rstrip(".").isdigit()]
    )


def test_n0_variants_differ_and_bad_variant_is_rejected() -> None:
    text = (BENCH / "skills" / "github-triage" / "SKILL.md").read_text(encoding="utf-8")
    assert controls.cosmetic(text, 1)[0] != controls.cosmetic(text, 2)[0]
    with pytest.raises(ValueError):
        controls.cosmetic(text, 3)


def test_d1_trigger_only_adds_a_blank_line() -> None:
    assert controls.d1_trigger("a\nb\n") == "a\nb\n\n"


@pytest.mark.parametrize("spec", controls.D1_SPECS, ids=lambda s: s.edit_id)
def test_every_d1_spec_moves_one_value_in_the_bench_world(spec: controls.D1Spec) -> None:
    override = controls.world_override(BENCH / "world", spec)
    before = json.loads((BENCH / "world" / spec.world_file).read_text(encoding="utf-8"))
    after = override[spec.world_file]
    assert after != before
    leaf_before, leaf_after = before, after
    for key in spec.path:
        leaf_before, leaf_after = leaf_before[key], leaf_after[key]  # type: ignore[index]
    assert controls.values_match(str(leaf_before), spec.old_value)
    assert controls.values_match(str(leaf_after), spec.new_value)
    # Nothing else moved.
    restored = controls.apply_override(after, spec.path, spec.new_value, spec.old_value)
    if isinstance(leaf_before, str):
        assert restored == before


def test_apply_override_refuses_missing_paths_and_values() -> None:
    with pytest.raises(KeyError):
        controls.apply_override({"a": {}}, ("a", "b"), "1", "2")
    with pytest.raises(ValueError):
        controls.apply_override({"a": "text"}, ("a",), "zzz", "2")


def test_values_match_tolerates_number_formatting() -> None:
    assert controls.values_match("1.0800", "1.08")
    assert controls.values_match("49", "$49")
    assert not controls.values_match("1.2702", "1.08")
    assert controls.values_match("Release 4.2 out", "4.2")


def _golden(facts: list[WorldFact]) -> Trace:
    env = EnvSnapshot(
        workspace_ref="w",
        frozen_time=datetime(2026, 10, 14, tzinfo=UTC),
        harness_version="minimal/0.1.0",
        skills_commit="abc",
    )
    return Trace(
        run_id="run_1",
        kind="golden",
        task_input="t",
        steps=[],
        final_output="o",
        artifacts_ref=None,
        skills_invoked=["fx-expense-converter"],
        world_dependent=True,
        world_facts=facts,
        env=env,
        agent_tier="super",
        seed=1,
        created_at=datetime(2026, 10, 14, tzinfo=UTC),
        golden_of=None,
        candidate_commit=None,
    )


def test_d1_fixtures_report_the_new_value_and_base_fixtures_the_old(tmp_path: Path) -> None:
    fact = WorldFact(
        fact_id="fx_eur_usd",
        statement="The EUR/USD exchange rate (1 EUR = 1.0800 USD on 2026-10-14)",
        source_step="t001",
        volatile=True,
        query_hint="EUR to USD exchange rate today",
        value="1.0800",
    )
    other = fact.model_copy(
        update={
            "fact_id": "gbp",
            "value": "1.2702",
            "query_hint": "GBP q",
            "statement": "GBP is 1.2702",
        }
    )
    spec = controls.D1_SPECS[0]
    moved = controls.fact_fixtures([_golden([fact, other])], as_of="2026-10-15", moved=spec)
    content = moved["EUR to USD exchange rate today"]["results"][0]["content"]  # type: ignore[index]
    assert "1.1630" in str(content) and "1.0800" not in str(content)
    assert "1.2702" in str(moved["GBP q"]["results"][0]["content"])  # type: ignore[index]
    base = controls.fact_fixtures([_golden([fact])], as_of="2026-10-15")
    index = controls.write_fixtures(tmp_path, base)
    assert list(index) == ["EUR to USD exchange rate today"]
    hits = asyncio.run(FixtureTavily(tmp_path).search("EUR to USD exchange rate today"))
    assert "1.0800" in hits.hits[0].content and "example.com" in hits.hits[0].url


def test_fixture_index_answers_the_query_the_drift_judge_sends(tmp_path: Path) -> None:
    """The judge searches ``current_query(hint)`` ("… today"), not the dated hint."""
    fact = WorldFact(
        fact_id="fx_eur_usd",
        statement="The EUR/USD exchange rate (1 EUR = 1.0800 USD on 2026-10-14)",
        source_step="t001",
        volatile=True,
        query_hint="EUR USD exchange rate 2026-10-14",
        value="1.0800",
    )
    base = controls.fact_fixtures([_golden([fact])], as_of="2026-10-15")
    index = controls.write_fixtures(tmp_path, base)
    assert set(index) == {"EUR USD exchange rate 2026-10-14", "EUR USD exchange rate today"}
    hits = asyncio.run(FixtureTavily(tmp_path).search("EUR USD exchange rate today"))
    assert "1.0800" in hits.hits[0].content
    older = fact.model_copy(
        update={
            "fact_id": "fx_old",
            "query_hint": "EUR USD exchange rate 2026-10-09",
            "value": "1.0700",
        }
    )
    both = controls.fact_fixtures([_golden([older, fact])], as_of="2026-10-15")
    controls.write_fixtures(tmp_path, both)
    hits = asyncio.run(FixtureTavily(tmp_path).search("EUR USD exchange rate today"))
    assert "1.0800" in hits.hits[0].content  # "today" is the latest recording, not the oldest


def test_fixture_index_matches_a_query_worded_from_an_observed_fact(tmp_path: Path) -> None:
    """ "EUR to USD exchange rate today" (observed fact) finds the "EUR USD …" fixture; a query
    naming another pair does not."""
    fact = WorldFact(
        fact_id="fx_eur_usd",
        statement="The EUR/USD exchange rate (1 EUR = 1.0800 USD on 2026-10-14)",
        source_step="t001",
        volatile=True,
        query_hint="EUR USD exchange rate 2026-10-14",
        value="1.0800",
    )
    controls.write_fixtures(tmp_path, controls.fact_fixtures([_golden([fact])], as_of="2026-10-15"))
    hits = asyncio.run(FixtureTavily(tmp_path).search("EUR to USD exchange rate today"))
    assert "1.0800" in hits.hits[0].content
    older = fact.model_copy(
        update={
            "fact_id": "fx_old",
            "query_hint": "EUR USD exchange rate 2026-10-09",
            "value": "1.0700",
        }
    )
    controls.write_fixtures(
        tmp_path, controls.fact_fixtures([_golden([older, fact])], as_of="2026-10-15")
    )
    hits = asyncio.run(FixtureTavily(tmp_path).search("EUR to USD exchange rate today"))
    assert "1.0800" in hits.hits[0].content  # several dated recordings: the latest answers
    with pytest.raises(UpstreamError) as err:
        asyncio.run(FixtureTavily(tmp_path).search("GBP to USD exchange rate today"))
    assert err.value.code == "drift_unavailable"
