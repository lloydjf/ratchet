"""Affected-trace selection — direct, neighborhood, canaries, cap of 40, batches of 10."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ratchet.errors import UpstreamError
from ratchet.models import EnvSnapshot, SkillEdit, Trace
from ratchet.orchestrator.embed import LocalHashEmbedder
from ratchet.orchestrator.selection import make_batches, select_traces, selection_counts
from ratchet.store import SelectedTrace, Store
from ratchet.watcher.edits import build_edit
from ratchet.watcher.gitops import SkillsRepo

T0 = datetime(2026, 10, 1, tzinfo=UTC)
OLD_DESC = "Triage GitHub issues: label each open issue by area and comment on duplicates."
NEW_DESC = OLD_DESC + " Also close stale issues with no activity for 30 days."


def skill_md(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\nInstructions.\n"


def golden(
    run_id: str,
    task: str,
    skills: Sequence[str] = (),
    *,
    age_days: int = 0,
    archived: str | None = None,
) -> Trace:
    created = T0 + timedelta(days=age_days)
    return Trace(
        run_id=run_id,
        kind="golden",
        task_input=task,
        steps=[],
        final_output="done",
        artifacts_ref=None,
        skills_invoked=list(skills),
        world_dependent=False,
        world_facts=[],
        env=EnvSnapshot(
            workspace_ref="ws.tar.zst",
            frozen_time=created,
            harness_version="minimal/0.1.0",
            skills_commit="abc",
        ),
        agent_tier="super",
        seed=7,
        created_at=created,
        golden_of=None,
        candidate_commit=None,
        archived_reason=archived,
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "data")


@pytest.fixture()
def repo(tmp_path: Path) -> SkillsRepo:
    seed = tmp_path / "seed"
    for name, desc in (
        ("github-triage", OLD_DESC),
        ("expense-report", "Build an expense report from receipts with the VAT line."),
    ):
        (seed / name).mkdir(parents=True)
        (seed / name / "SKILL.md").write_text(skill_md(name, desc))
    skills = SkillsRepo(tmp_path / "skills")
    skills.init(seed)
    return skills


@pytest.fixture()
def edit(repo: SkillsRepo) -> SkillEdit:
    commit = repo.apply_files(
        {"github-triage/SKILL.md": skill_md("github-triage", NEW_DESC)}, message="learn"
    )
    return build_edit(repo, commit=commit, author="agent")[0]


def by_reason(selected: list[SelectedTrace]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in selected:
        out.setdefault(item.reason, []).append(item.run_id)
    return out


async def test_direct_neighborhood_and_canaries(
    store: Store, repo: SkillsRepo, edit: SkillEdit
) -> None:
    for trace in (
        golden("run_direct1", "Triage acme/widgets", ["github-triage"], age_days=5),
        golden("run_direct2", "Label issues", ["github-triage", "expense-report"], age_days=6),
        golden("run_other", "Expense report for October", ["expense-report"], age_days=1),
        golden("run_near_old", "Comment on duplicate GitHub issues and label them by area"),
        golden("run_near_new", "Close the stale issues with no activity", age_days=2),
        golden("run_far", "Book a meeting with Dana next Tuesday", age_days=3),
        golden("run_c1", "Summarize this PDF", age_days=-30),
        golden("run_c2", "Rename my photos by date", age_days=-20),
        golden("run_c3", "Translate this paragraph to French", age_days=-10),
        golden("run_archived", "Triage the backlog", ["github-triage"], archived="drift"),
    ):
        store.save_trace(trace)

    selected = await select_traces(store, repo, edit, LocalHashEmbedder())
    groups = by_reason(selected)

    assert groups["direct"] == ["run_direct2", "run_direct1"]  # newest first
    assert set(groups["neighborhood"]) == {"run_near_old", "run_near_new"}
    assert groups["canary"] == ["run_c1", "run_c2", "run_c3"]
    assert "run_archived" not in {s.run_id for s in selected}
    assert "run_far" not in {s.run_id for s in selected}
    assert [s.reason for s in selected] == ["direct"] * 2 + ["neighborhood"] * 2 + ["canary"] * 3
    sims = [s.similarity for s in selected if s.reason == "neighborhood"]
    assert None not in sims
    assert sims == sorted((x or 0.0 for x in sims), reverse=True)
    assert selection_counts(selected) == {
        "n_traces": 7,
        "n_direct": 2,
        "n_neighborhood": 2,
        "n_canary": 3,
    }


async def test_neighborhood_compares_old_and_new_descriptions(
    store: Store, repo: SkillsRepo, edit: SkillEdit
) -> None:
    store.save_trace(golden("run_stale", "Close the stale issues with no activity"))
    only_new = await select_traces(store, repo, edit, LocalHashEmbedder())
    assert [s.reason for s in only_new] == ["neighborhood"]

    narrowed = edit.model_copy(update={"commit": edit.parent, "parent": edit.commit})
    swapped = await select_traces(store, repo, narrowed, LocalHashEmbedder())
    assert [s.reason for s in swapped] == ["neighborhood"]  # old description still counts


async def test_flagged_canaries_win_and_are_topped_up(
    store: Store, repo: SkillsRepo, edit: SkillEdit
) -> None:
    for i in range(5):
        store.save_trace(golden(f"run_free{i}", f"Unrelated chore {i}", age_days=i))
    selected = await select_traces(
        store, repo, edit, LocalHashEmbedder(), canaries=["run_free4", "run_missing"]
    )
    assert by_reason(selected)["canary"] == ["run_free4", "run_free0", "run_free1"]


async def test_no_affected_traces_still_returns_canaries(store: Store, repo: SkillsRepo) -> None:
    store.save_trace(golden("run_free", "Translate a paragraph"))
    base = repo.main_head()
    commit = repo.apply_files({"README.md": "hello\n"}, message="docs")
    edit = build_edit(repo, commit=commit, parent=base, author="user")[0]
    assert edit.affected_skills == []
    selected = await select_traces(store, repo, edit, LocalHashEmbedder())
    assert [(s.run_id, s.reason) for s in selected] == [("run_free", "canary")]


class ScriptedEmbedder:
    """Descriptions embed to e1; a task "sim=<x>" at cosine x from it; other text is orthogonal."""

    name = "scripted"
    default_threshold = 0.5

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            if text.startswith("sim="):
                x = float(text.split("=", 1)[1])
                vectors.append([x, math.sqrt(max(1 - x * x, 0.0))])
            elif text in (OLD_DESC, NEW_DESC):
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


async def test_cap_keeps_all_direct_then_best_neighbors(
    store: Store, repo: SkillsRepo, edit: SkillEdit
) -> None:
    for i in range(35):
        store.save_trace(golden(f"run_d{i:02d}", "triage", ["github-triage"], age_days=i))
    for i, sim in enumerate((0.55, 0.95, 0.75, 0.65, 0.85, 0.60, 0.90, 0.70)):
        store.save_trace(golden(f"run_n{i}", f"sim={sim}"))
    store.save_trace(golden("run_low", "sim=0.2"))
    for i in range(3):
        store.save_trace(golden(f"run_c{i}", "plain chore", age_days=-i - 1))

    selected = await select_traces(store, repo, edit, ScriptedEmbedder(), cap=40)

    assert len(selected) == 40
    assert [s.reason for s in selected[:35]] == ["direct"] * 35
    assert selected[0].run_id == "run_d34"  # newest direct first
    tail = [(s.run_id, s.similarity) for s in selected[35:]]
    assert tail == [
        ("run_n1", 0.95),
        ("run_n6", 0.9),
        ("run_n4", 0.85),
        ("run_n2", 0.75),
        ("run_n7", 0.7),
    ]
    assert not by_reason(selected).get("canary")


async def test_cap_of_forty_on_direct_only(store: Store, repo: SkillsRepo, edit: SkillEdit) -> None:
    for i in range(45):
        store.save_trace(golden(f"run_d{i:02d}", "triage", ["github-triage"], age_days=i))
    selected = await select_traces(store, repo, edit, LocalHashEmbedder())
    assert len(selected) == 40
    assert {s.reason for s in selected} == {"direct"}
    assert [len(b) for b in make_batches(selected)] == [10, 10, 10, 10]


async def test_explicit_threshold_overrides_the_embedder_default(
    store: Store, repo: SkillsRepo, edit: SkillEdit
) -> None:
    store.save_trace(golden("run_n", "sim=0.6", ["expense-report"]))
    assert len(await select_traces(store, repo, edit, ScriptedEmbedder())) == 1
    assert await select_traces(store, repo, edit, ScriptedEmbedder(), threshold=0.7) == []


class BrokenEmbedder:
    name = "broken"
    default_threshold = 0.99

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise UpstreamError("embeddings down")


async def test_embedder_failure_falls_back_to_local_hash(
    store: Store, repo: SkillsRepo, edit: SkillEdit
) -> None:
    store.save_trace(
        golden("run_near", "Comment on duplicate GitHub issues and label them", ["expense-report"])
    )
    selected = await select_traces(store, repo, edit, BrokenEmbedder())
    assert [s.reason for s in selected] == ["neighborhood"]


def test_make_batches_groups_of_ten() -> None:
    ids = [f"run_{i}" for i in range(23)]
    batches = make_batches(ids)
    assert [len(b) for b in batches] == [10, 10, 3]
    assert [x for b in batches for x in b] == ids
    assert make_batches([]) == []
    assert make_batches([SelectedTrace("run_a", "direct", None)], size=1) == [["run_a"]]
    with pytest.raises(ValueError):
        make_batches(ids, size=0)
