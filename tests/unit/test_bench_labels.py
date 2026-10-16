"""labels.json schema, edit directories, the review sample and recorded verdicts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ratchet.bench import labels as bl
from ratchet.bench.cli_support import label_cmd, parse_pairs
from ratchet.bench.dataset import BenchPaths
from ratchet.errors import ConfigError


def _label(edit_id: str, type_: str = "R2", **kw: object) -> bl.EditLabel:
    decision = "PASS" if type_ in ("N0", "D1") else "BLOCK"
    return bl.EditLabel.model_validate(
        {
            "edit_id": edit_id,
            "skill": "expense-report",
            "type": type_,
            "expected_decision": decision,
            **kw,
        }
    )


def test_label_schema_round_trips_and_rejects_unknown_fields(tmp_path: Path) -> None:
    lab = _label("r2-x", affected_goldens=["t1"], expected_pair_outcomes={"t1": "regression"})
    path = tmp_path / "labels.json"
    bl.save_labels(path, bl.LabelSet(labels=[lab]))
    raw = json.loads(path.read_text())
    assert set(raw["labels"][0]) >= {
        "edit_id",
        "skill",
        "type",
        "affected_goldens",
        "expected_decision",
        "expected_pair_outcomes",
        "review",
    }
    again = bl.load_labels(path).get("r2-x")
    assert again == lab
    assert again.expected_outcome("t1") == "regression" and again.expected_outcome("zz") == "pass"
    with pytest.raises(ValidationError):
        bl.EditLabel.model_validate({**lab.model_dump(), "surprise": 1})
    with pytest.raises(ValidationError):
        _label("bad", type_="R9")


def test_effective_decision_prefers_a_reviewers_correction() -> None:
    labels = bl.LabelSet(labels=[_label("r1-x", "R1")])
    labels = bl.record_review(labels, "r1-x", agree=False, reviewer="ann", decision="PASS")
    lab = labels.get("r1-x")
    assert lab.review.status == "disagreed" and lab.effective_decision() == "PASS"
    labels = bl.record_review(
        labels, "r1-x", agree=True, reviewer="ann", pair_labels={"t1": "pass"}
    )
    assert labels.get("r1-x").effective_decision() == "BLOCK"
    assert labels.get("r1-x").review.pair_labels == {"t1": "pass"}
    with pytest.raises(ConfigError):
        bl.record_review(labels, "r1-x", agree=False, reviewer="ann")


def test_review_sample_covers_all_controls_and_a_fifth_of_each_regression_type() -> None:
    regressions = [_label(f"r{t}-{i}", f"R{t}") for t in range(1, 7) for i in range(10)]
    controls = [_label(f"n0-{i}", "N0") for i in range(4)] + [_label("d1-0", "D1")]
    sampled = bl.mark_review_sample(bl.LabelSet(labels=regressions + controls), seed=7)
    required = {lab.edit_id for lab in sampled.labels if lab.review.required}
    assert {c.edit_id for c in controls} <= required
    for t in range(1, 7):
        assert sum(1 for r in required if r.startswith(f"r{t}-")) == 2
    again = bl.mark_review_sample(bl.LabelSet(labels=regressions + controls), seed=7)
    assert required == {lab.edit_id for lab in again.labels if lab.review.required}
    assert all(lab.review.status == "pending" for lab in sampled.labels if lab.review.required)


def test_edit_directory_round_trip_and_diff(tmp_path: Path) -> None:
    skills = tmp_path / "skills" / "expense-report"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: expense-report\ndescription: x\n---\nold\n")
    root = bl.write_edit(
        tmp_path / "edits",
        "d1-x",
        {"expense-report/SKILL.md": "---\nname: expense-report\ndescription: x\n---\nnew\n"},
        world={"fx.json": {"rates": {}}},
        tavily={"index.json": {}},
    )
    assert bl.edit_files(tmp_path / "edits", "d1-x") == {
        "expense-report/SKILL.md": "---\nname: expense-report\ndescription: x\n---\nnew\n"
    }
    assert bl.edit_world(tmp_path / "edits", "d1-x") == root / "world"
    assert bl.edit_tavily(tmp_path / "edits", "d1-x") == root / "tavily"
    diff = bl.edit_diff(tmp_path / "edits", tmp_path / "skills", "d1-x")
    assert "-old" in diff and "+new" in diff
    with pytest.raises(ConfigError):
        bl.edit_dir(tmp_path, "../escape")


def test_label_cli_shows_the_diff_and_records_a_verdict(tmp_path: Path) -> None:
    paths = BenchPaths(tmp_path)
    (tmp_path / "skills" / "expense-report").mkdir(parents=True)
    (tmp_path / "skills" / "expense-report" / "SKILL.md").write_text("a\n")
    bl.write_edit(paths.edits, "n0-x", {"expense-report/SKILL.md": "b\n"})
    bl.save_labels(paths.labels, bl.mark_review_sample(bl.LabelSet(labels=[_label("n0-x", "N0")])))
    out: list[str] = []
    label_cmd(
        paths,
        None,
        agree=None,
        decision=None,
        pairs=[],
        reviewer="r",
        note="",
        status_only=True,
        echo=out.append,
        interactive=False,
    )
    assert "1 pending of 1 required" in out[0]
    label_cmd(
        paths,
        None,
        agree=True,
        decision=None,
        pairs=["t1=pass"],
        reviewer="ann",
        note="ok",
        status_only=False,
        echo=out.append,
        interactive=False,
    )
    assert any("+b" in line for line in out)
    lab = bl.load_labels(paths.labels).get("n0-x")
    assert lab.review.status == "agreed" and lab.review.reviewer == "ann"
    assert lab.review.pair_labels == {"t1": "pass"}


def test_parse_pairs_rejects_unknown_labels() -> None:
    assert parse_pairs(["a=drift"]) == {"a": "drift"}
    with pytest.raises(ConfigError):
        parse_pairs(["a=maybe"])
