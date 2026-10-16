"""Bless-time enrichment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import FakeModelClient, factory, settings  # noqa: E402

from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.judge import bless  # noqa: E402
from ratchet.models import ToolCall  # noqa: E402
from ratchet.store import Store  # noqa: E402

FACTS = json.dumps(
    {
        "facts": [
            {"fact_id": "rate", "statement": "USD/EUR was 0.92 on 2026-10-14",
             "source_step": "t001", "volatile": True, "query_hint": "USD EUR rate",
             "value": "0.92"},
            {"fact_id": "rate", "statement": "Hotel cost USD 200", "source_step": "t777",
             "volatile": False, "query_hint": ""},
        ]
    }
)  # fmt: skip
CRITERIA = json.dumps({"criteria": ["Hotel converted at the day's rate", " ", "Row added"]})


async def test_enrich_adds_facts_criteria_world_dependence_and_blessed_at(tmp_path: Path) -> None:
    golden = factory.by_name("d1_fx_drift").golden
    bare = golden.model_copy(
        update={"world_facts": [], "success_criteria": [], "world_dependent": False}
    )
    client = (
        FakeModelClient().add("extract_world_facts", FACTS).add("infer_success_criteria", CRITERIA)
    )
    with settings(tmp_path):
        store = Store.open(tmp_path / "data")
        out = await bless.enrich(bare, client, store)
    # The second fact cites no world-tool step, so the grounding filter drops it.
    assert [f.fact_id for f in out.world_facts] == ["rate"]
    assert out.world_facts[0].value == "0.92" and out.world_facts[0].source_step == "t001"
    assert out.success_criteria == ["Hotel converted at the day's rate", "Row added"]
    assert out.world_dependent and out.blessed_at is not None
    assert client.tiers == ["nano", "nano"]
    extract = client.calls_for("extract_world_facts")[0].user_text
    assert 't001 finance.rate: {"rate": 0.92}' in extract


async def test_world_tools_make_a_trace_world_dependent_without_facts(tmp_path: Path) -> None:
    golden = factory.by_name("d1_no_drift").golden.model_copy(update={"world_facts": []})
    client = FakeModelClient().add("extract_world_facts", '{"facts": []}')
    with settings(tmp_path):
        out = await bless.enrich(golden, client, Store.open(tmp_path / "data"))
    assert out.world_facts == [] and out.world_dependent
    assert client.calls_for("infer_success_criteria") == []  # criteria already present


async def test_failed_extraction_leaves_parts_empty_and_unmatched_tools_are_classified(
    tmp_path: Path,
) -> None:
    golden = factory.by_name("n0_cosmetic").golden
    odd = ToolCall.model_validate(
        {
            **golden.steps[0].model_dump(),
            "step_id": "t003",
            "tool": "crm.update",
            "side_effect": "read",
        }
    )
    trace = golden.model_copy(update={"steps": [*golden.steps, odd], "success_criteria": []})
    client = (
        FakeModelClient()
        .add("classify_side_effect", '{"side_effect": "write_reversible", "reason": "updates"}')
        .add("extract_world_facts", UpstreamError("down"))
        .add("infer_success_criteria", UpstreamError("down"))
    )
    with settings(tmp_path):
        out = await bless.enrich(trace, client, Store.open(tmp_path / "data"))
    assert out.world_facts == [] and out.success_criteria == [] and not out.world_dependent
    classes = {s.tool: s.side_effect for s in out.steps if isinstance(s, ToolCall)}
    assert classes == {
        "git.log": "read",
        "fs.write": "write_reversible",
        "crm.update": "write_reversible",
    }
    assert "fs.write CHANGELOG.md" not in client.calls_for("classify_side_effect")[0].user_text


def test_grounding_keeps_only_world_tool_facts_whose_value_is_in_the_result() -> None:
    from ratchet.judge.bless import _grounded, value_in
    from ratchet.judge.schemas import WorldFactOut

    golden = factory.by_name("d1_fx_drift").golden  # t001 is finance.rate {"rate": 0.92}

    def fact(value: str, step: str) -> WorldFactOut:
        return WorldFactOut(
            fact_id=value,
            statement=f"x was {value}",
            source_step=step,
            volatile=True,
            query_hint="q",
            value=value,
        )

    kept = _grounded([fact("0.9200", "t001"), fact("50.00", "t001"), fact("0.92", "")], golden)
    assert [f.value for f in kept] == ["0.9200"]
    assert value_in("1.0800", '{"rate": 1.08}') and not value_in("1.09", '{"rate": 1.08}')
    assert not value_in("", "anything")


def test_fact_value_falls_back_to_the_statement() -> None:
    from ratchet.judge.bless import fact_value

    assert fact_value("", "The EUR/USD exchange rate on 2026-10-14 was 1.0800") == "1.0800"
    assert fact_value(" 0.92 ", "ignored") == "0.92"
    assert fact_value("", "no number here") == ""


def test_grounding_dedupes_and_drops_observation_dates() -> None:
    from ratchet.judge.bless import _grounded
    from ratchet.judge.schemas import WorldFactOut

    golden = factory.by_name("d1_fx_drift").golden

    def fact(value: str) -> WorldFactOut:
        return WorldFactOut(
            fact_id="f",
            statement=f"x was {value}",
            source_step="t001",
            volatile=True,
            query_hint="q",
            value=value,
        )

    assert [
        f.value for f in _grounded([fact("0.92"), fact("0.92"), fact("2026-10-14")], golden)
    ] == ["0.92"]


def test_value_in_ignores_units_and_formatting() -> None:
    from ratchet.judge.bless import value_in

    assert value_in("1.11969431 USD", "1.00 EUR = 1.11969431 US Dollars")
    assert value_in("$1.12", "rate is 1.12 today")
    assert not value_in("1.13 USD", "rate is 1.12 today")
    assert not value_in("1.12 to 1.13", "rate is 1.12 today")  # two numbers: textual only


def test_formatting_criteria_are_filtered_out_at_bless_time() -> None:
    from ratchet.judge.bless import is_format_criterion

    assert is_format_criterion('output includes the phrase "= **40.8**"')
    assert is_format_criterion("the result is shown in bold")
    assert is_format_criterion("the file name appears in a `code span`")
    assert not is_format_criterion("the output states the total 40.8")
    assert not is_format_criterion("one row per receipt is appended to the Expenses sheet")
