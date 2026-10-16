"""Bench wiring for control replays (ADR 0023): the configs' switch, the re-judge reuse of the
source run's controls, and the noise-floor line."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_fixtures import small_results  # noqa: E402
from orch_fakes import commit_edit, make_settings, open_store, seeded_repo  # noqa: E402

from ratchet.bench import execute  # noqa: E402
from ratchet.bench.dataset import load_config  # noqa: E402
from ratchet.bench.report import ReportInputs, render  # noqa: E402
from ratchet.orchestrator.context import (  # noqa: E402
    EditOptions,
    attempt_dir,
    load_options,
    save_options,
)

CONFIGS = Path(__file__).resolve().parents[2] / "bench" / "configs"


@pytest.mark.parametrize(
    ("name", "on"),
    [("cascade", True), ("noise", True), ("level0_off", True), ("drift_off", True)]
    + [("controls_off", False)],
)
def test_control_replays_are_on_except_in_the_ablation(name: str, on: bool) -> None:
    config = load_config(CONFIGS / f"{name}.yaml")
    assert config.cascade.controls is on
    if name == "controls_off":
        assert config.reuse_replays == "cascade" and config.cascade.mode == "cascade"


def test_a_rejudge_copies_the_source_controls_and_never_replays_its_own(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    repo = seeded_repo(settings)
    ids = []
    for name in ("a", "b"):
        edit, diff = commit_edit(repo, {f"expense-report/{name}.md": "x\n"})
        ids.append(store.save_edit(edit, diff).edit_id)
        save_options(store, ids[-1], EditOptions())
    source, target = ids
    saved = attempt_dir(store, source, 1) / "controls" / "run_g.json"
    saved.parent.mkdir(parents=True)
    saved.write_text("{}", encoding="utf-8")
    execute._clone_controls(SimpleNamespace(store=store), target, source, 1)  # type: ignore[arg-type]
    assert (attempt_dir(store, target, 1) / "controls" / "run_g.json").is_file()
    assert load_options(store, target).reuse_controls


def test_the_noise_floor_line_names_control_replays_only_when_they_ran() -> None:
    results = small_results(simulated=True)

    def text(controls: bool) -> str:
        noise = results.runs["noise"]
        cascade = noise.config.cascade.model_copy(update={"controls": controls})
        config = noise.config.model_copy(update={"cascade": cascade})
        runs = {**results.runs, "noise": noise.model_copy(update={"config": config})}
        inputs = ReportInputs(
            results=results.model_copy(update={"runs": runs}),
            out_dir=Path("bench/results/x"),
            date="2026-10-16",
            diffs={},
            human={},
            label_counts={},
            reviewed=(0, 0),
        )
        return render(inputs)[0]

    assert "unchanged-skill pairs) with control replays" in text(True)
    assert "with control replays" not in text(False)
