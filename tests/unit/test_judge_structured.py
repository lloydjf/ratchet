"""Structured-output helper: valid JSON passes; invalid JSON is retried
twice, then the caller gets ``schema_failure`` (→ ``uncertain``). Also JSON extraction and
nonce-delimited untrusted inputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import FakeModelClient, opinion, settings  # noqa: E402

from ratchet.errors import BudgetExceeded, UpstreamError  # noqa: E402
from ratchet.judge import prompts, structured  # noqa: E402
from ratchet.judge.schemas import ScreenOut  # noqa: E402

SCREEN_INPUTS = {
    "task_input": "Triage acme/widgets",
    "criteria": "- #12 labeled bug",
    "golden_output": "Labeled #12 bug.",
    "replay_output": "Labeled #12 bug.",
    "golden_calls": "t001 github.label (write_reversible)",
    "replay_calls": "t001 github.label (write_reversible)",
}


async def _screen(client: FakeModelClient, **kw: object) -> structured.StructuredResult[ScreenOut]:
    return await structured.call_structured(
        client,
        prompt="screen",
        tier="nano",
        inputs=kw.pop("inputs", SCREEN_INPUTS),  # type: ignore[arg-type]
        schema=ScreenOut,
        max_tokens=300,
        **kw,  # type: ignore[arg-type]
    )


async def test_valid_json_passes_on_first_attempt(tmp_path: Path) -> None:
    """Valid JSON passes on the first attempt."""
    with settings(tmp_path):
        client = FakeModelClient().add("screen", opinion("equivalent", 0.9, evidence=["t001"]))
        result = await _screen(client)
    assert result.value is not None and result.value.verdict == "equivalent"
    assert result.attempts == 1 and result.failure_code is None
    assert result.usd > 0 and result.prompt_tokens > 0
    call = client.calls[0]
    assert call.response_format is not None and call.response_format["type"] == "json_schema"
    schema_block = call.response_format["json_schema"]
    assert isinstance(schema_block, dict) and schema_block["strict"] is True
    assert schema_block["name"] == "screen"
    assert call.prompt_name == f"screen@{prompts.prompt_hash('screen')[:12]}"
    assert call.purpose == "screen" and call.thinking is False


async def test_invalid_json_is_retried_twice_then_schema_failure(tmp_path: Path) -> None:
    """Invalid JSON is retried twice, then ``schema_failure``."""
    with settings(tmp_path):
        client = FakeModelClient().add("screen", "not json", '{"verdict": "maybe"}', "{}")
        result = await _screen(client)
    assert result.value is None and result.failure_code == "schema_failure"
    assert result.attempts == 3 and len(client.calls) == 3
    retry = client.calls[1].messages
    assert retry[-2]["role"] == "assistant" and retry[-1]["role"] == "user"
    assert "not valid" in str(retry[-1]["content"])
    assert "verdict" in str(client.calls[2].messages[-1]["content"])


async def test_retry_recovers_and_sums_usage(tmp_path: Path) -> None:
    with settings(tmp_path):
        client = FakeModelClient().add("screen", "oops", opinion("regressed", 0.95))
        result = await _screen(client)
    assert result.value is not None and result.attempts == 2
    assert result.prompt_tokens > 0 and result.completion_tokens >= 2


@pytest.mark.parametrize(
    "raw",
    [
        "```json\n" + opinion("equivalent", 0.9) + "\n```",
        "<think>Let me compare {the outputs}.</think>" + opinion("equivalent", 0.9),
        "reasoning first...</think>\n" + opinion("equivalent", 0.9),
        "Here you go: " + opinion("equivalent", 0.9) + " hope that helps",
        opinion("equivalent", 90),
    ],
)
async def test_json_is_extracted_from_noisy_replies(tmp_path: Path, raw: str) -> None:
    with settings(tmp_path):
        result = await _screen(FakeModelClient().add("screen", raw))
    assert result.value is not None and result.value.confidence == pytest.approx(0.9)


def test_extract_json_rejects_text_without_an_object() -> None:
    with pytest.raises(ValueError):
        structured.extract_json("[1, 2, 3] and nothing else")


async def test_upstream_error_is_not_retried_and_returns_its_code(tmp_path: Path) -> None:
    with settings(tmp_path):
        client = FakeModelClient().add("screen", UpstreamError("down", code="upstream_unavailable"))
        result = await _screen(client)
    assert result.value is None and result.failure_code == "upstream_unavailable"
    assert len(client.calls) == 1 and result.attempts == 0


async def test_budget_exceeded_returns_budget_code(tmp_path: Path) -> None:
    with settings(tmp_path):
        result = await _screen(FakeModelClient().add("screen", BudgetExceeded("cap")))
    assert result.failure_code == "budget_exceeded"


def _rejected() -> UpstreamError:
    return UpstreamError(
        "model rejected the request (400)",
        code="upstream_rejected",
        retryable=False,
        context={"status": 400},
    )


async def test_rejected_json_schema_degrades_to_json_object_then_prompt(tmp_path: Path) -> None:
    with settings(tmp_path):
        client = FakeModelClient().add("screen", _rejected(), _rejected(), opinion("pass", 0.1))
        client.add("screen", opinion("equivalent", 0.9))
        result = await _screen(client)
        assert [c.response_format and c.response_format["type"] for c in client.calls] == [
            "json_schema",
            "json_object",
            None,
            None,
        ]
        assert result.value is not None and result.mode == "prompt"
        assert "JSON schema" in client.calls[2].user_text  # prompt-only mode spells it out
        # The degraded mode is cached per tier: the next nano call skips the failing modes.
        again = FakeModelClient().add("screen", opinion("equivalent", 0.9))
        await _screen(again)
        assert again.calls[0].response_format is None
        sup = FakeModelClient().add("screen", opinion("equivalent", 0.9))
        await structured.call_structured(
            sup, prompt="screen", tier="super", inputs=SCREEN_INPUTS, schema=ScreenOut,
            max_tokens=300,
        )  # fmt: skip
        assert sup.calls[0].response_format is not None


async def test_explicit_mode_never_degrades(tmp_path: Path) -> None:
    with settings(tmp_path, ratchet_structured_mode="json_object"):
        client = FakeModelClient().add("screen", _rejected())
        result = await _screen(client)
    assert result.failure_code == "upstream_rejected" and len(client.calls) == 1
    assert client.calls[0].response_format == {"type": "json_object"}


def test_render_fails_on_missing_variables_and_does_not_rerender_inputs() -> None:
    with pytest.raises(ValueError, match="task_input"):
        structured.render("Task input: {{task_input}}", {})
    out = structured.render("A {{a}} B {{b}}", {"a": "{{b}}", "b": "x"})
    assert out == "A {{b}} B x"


def test_judge_prompts_end_with_the_shared_tail_and_narrative_does_not() -> None:
    screen = structured.build_prompt("screen", SCREEN_INPUTS)
    assert screen.text.endswith(prompts.SHARED_TAIL)
    narrative = prompts.load_body("narrative")
    assert "Rules:" not in narrative


def test_inputs_are_truncated_to_the_context_budget() -> None:
    big = dict(SCREEN_INPUTS, golden_output="g" * 5000, replay_output="r" * 1000)
    rendered = structured.build_prompt("screen", big)
    assert rendered.truncated  # 2,500-char cap per output
    assert "[truncated]" in rendered.text
    tight = structured.build_prompt(
        "screen", dict(SCREEN_INPUTS, criteria="c" * 9000), context_budget_tokens=900
    )
    assert tight.truncated and structured.estimate_tokens(tight.text) <= 1000


def test_few_shot_examples_only_under_sixty_percent_of_budget() -> None:
    small = structured.build_prompt("screen", SCREEN_INPUTS)
    assert small.examples is not None and "Worked examples" in small.examples
    large = structured.build_prompt("screen", dict(SCREEN_INPUTS, criteria="c" * 20_000))
    assert large.examples is None
    assert structured.build_prompt("adjudicate", _adjudicate_inputs()).examples is None


async def test_examples_go_in_a_system_message_without_live_section_labels(tmp_path: Path) -> None:
    with settings(tmp_path):
        client = FakeModelClient().add("screen", opinion("equivalent", 0.9))
        await _screen(client)
    system = client.calls[0].system_text
    assert "Worked examples" in system
    for label in ("Golden final output:", "Replay final output:", "Task input:"):
        assert label not in system
        assert client.calls[0].user_text.count(label) == 1


def _adjudicate_inputs() -> dict[str, str]:
    keys = [
        "task_input", "skill_diff", "criteria", "golden_steps", "replay_steps", "golden_output",
        "replay_output", "level1", "level2",
    ]  # fmt: skip
    return dict.fromkeys(keys, "x")


async def test_thinking_prompt_adds_reasoning_budget_to_max_tokens(tmp_path: Path) -> None:
    from ratchet.judge.schemas import AdjudicateOut

    with settings(tmp_path):
        client = FakeModelClient().add("adjudicate", opinion("regressed", 0.9))
        await structured.call_structured(
            client, prompt="adjudicate", tier="ultra", inputs=_adjudicate_inputs(),
            schema=AdjudicateOut, max_tokens=1200, reasoning_budget=4000,
        )  # fmt: skip
    call = client.calls[0]
    assert call.thinking is True and call.reasoning_budget == 4000 and call.max_tokens == 5200


def test_prompt_section_labels_are_verbatim() -> None:
    expected = {
        "screen": ["Task input:", "Success criteria:", "Golden final output:",
                   "Replay final output:", "Golden tool calls (tool, side-effect class):",
                   "Replay tool calls (tool, side-effect class):"],
        "check": ["Skill diff (unified):", "Golden run, step by step", "Replay run, step by step:",
                  "Golden final output:", "Replay final output:", "A first-pass judge said:"],
        "adjudicate": ["Earlier opinions:", "- nano:", "- super:", "Golden final output:"],
        "extract_world_facts": ["Task input:", "Final output:", "Tool results"],
        "classify_side_effect": ["Tool:", "Example arguments:"],
        "infer_success_criteria": ["Task input:", "Final output:", "Artifacts changed:"],
        "attribute_drift": ["Golden final output:", "Replay final output:"],
        "narrative": ["Decision:", "Action taken:", "Strongest evidence per non-pass pair:"],
        "inject_regression": ["Original SKILL.md:"],
    }  # fmt: skip
    for name, labels in expected.items():
        body = prompts.load_body(name)
        for label in labels:
            assert label in body, (name, label)


def test_committed_schema_files_match_the_models(tmp_path: Path) -> None:
    rendered = prompts.export_schemas(tmp_path)
    for name, text in rendered.items():
        committed = (prompts.PROMPTS_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
        assert committed == text, f"{name}.schema.json is stale; run export_schemas()"
        assert json.loads(text)["type"] == "object"


def test_every_registered_prompt_has_a_body_and_unknown_names_fail() -> None:
    for name in prompts.PROMPTS:
        assert prompts.load_body(name)
        assert len(prompts.prompt_hash(name)) == 64
    with pytest.raises(ValueError):
        prompts.get_spec("nope")
    with pytest.raises(ValueError):
        prompts.load_body("../etc")
