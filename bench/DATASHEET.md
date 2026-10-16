# SkillRegress-Bench — datasheet

A labeled benchmark for **skill regression in self-improving agents**: an agent edits one of
its own skills (an agentskills.io `SKILL.md`), and the question is whether the edit broke a
task the skill used to do right. Each case pairs a skill edit with the blessed golden traces
it affects and a ground-truth decision. Built for Ratchet; the
format follows "Datasheets for Datasets" (Gebru et al.).

**License:** data (skills, tasks, world, workspaces, edits, labels, fixtures, results) is
CC BY 4.0; code (`ratchet/bench/`) is Apache-2.0.

## Motivation

Self-improving agents (Hermes-style skill libraries, OpenClaw) rewrite their own procedures.
Each rewrite is an untested deploy. SkillRegress-Bench measures how well a regression gate
catches the edits that break past tasks, how often it blocks edits that do not, and what the
judging costs — with world drift (the environment moved, the skill did not) as a separate
control, because a naive gate cries wolf on it.

## Composition

| Part | Count | Where |
| --- | --- | --- |
| Skills | 10 (listed below) | `bench/skills/<name>/SKILL.md` |
| Task inputs | 100 (10 per skill, 2 edge cases each) + 3 skill-free canaries | `bench/tasks.yaml` |
| Initial workspaces | 45 directories | `bench/workspaces/` |
| World | 12 GitHub repos, 10 git logs, 14 receipts, 4 FX pairs, 10 calendar events + busy blocks, a mailbox, 23 news/reference pages with 10 canned searches, 10 package changelogs | `bench/world/` |
| Goldens | 103 recorded + blessed traces (one per task) | built into the bench store (`var/bench/data`) |
| Regression edits | up to 120: 10 skills × R1–R6 × 2 variants, LLM-injected, manifestation-checked | `bench/edits/`, `bench/labels/labels.json` |
| N0 controls | 20 (2 per skill), deterministic cosmetic edits | same |
| D1 controls | 6 (2 per world-dependent skill), world overlay + Tavily fixtures | same |
| Configs | 7 (`cascade`, `nano_only`, `super_only`, `ultra_only`, `drift_off`, `level0_off`, `noise`) | `bench/configs/` |

Skills and their tools (all mocked; no live side effects):

| Skill | Tools | World-dependent |
| --- | --- | --- |
| expense-report | `receipts.parse`, `sheets.append`, `gmail.send` | no |
| github-triage | `github.list_issues`, `github.label`, `github.comment`, `github.close_issue` | no |
| calendar-scheduler | `calendar.freebusy`, `calendar.create`, `gmail.send` | no |
| changelog-writer | `git.log`, `fs.read`, `fs.write` | no |
| csv-cleaner | `fs.read`, `fs.write`, `shell.run` (read-only allowlist) | no |
| invoice-parser | `fs.read`, `sheets.append` | no |
| weekly-research-brief | `web.search`, `web.fetch`, `fs.write` | yes |
| fx-expense-converter | `finance.rate`, `sheets.append` | yes |
| dependency-updater | `shell.run`, `web.fetch` (changelogs), `fs.write`, `github.open_pr` | yes |
| meeting-notes | `fs.read`, `fs.write`, `gmail.send` | no |

Deviations from each skill's minimal tool list, all additive: changelog-writer also reads the existing
`CHANGELOG.md` (`fs.read`); dependency-updater reads changelog pages with `web.fetch` (that is
what makes it world-dependent); meeting-notes writes the minutes file (`fs.write`) and sends
them only when the task asks (`gmail.send`). The five skills shared with the demo agent
(expense-report, github-triage, fx-expense-converter, meeting-notes, changelog-writer) start
from `scenarios/skills/` and keep the phrasing the offline simulator's policies read.

## Collection and construction

1. **Tasks** are hand-written (varied phrasing; 2 edge cases per skill: a missing file, an
   empty input, a weekend rate date, an already-labeled backlog, no search results, …). Every
   task routes to its skill under MinimalRunner's lexical trigger matcher
   (`ratchet bench validate`; `tests/unit/test_bench_dataset.py`).
2. **Goldens:** each task runs once on the demo agent (MinimalRunner → capture proxy → Nemotron
   Super, mock tools in record mode against `bench/world`, frozen at 2026-10-14T16:00:00Z,
   seed = hash of the task id) and is blessed (PII pass, Nano world facts and criteria).
3. **Regressions (R1–R6):** Nemotron Ultra edits each skill with `inject_regression.md`,
   given the regression type's definition. A variant is kept only if it is a valid
   skill with the same name and **manifests**: replaying it makes at least one selected golden
   diverge (output, new/missing calls or changed arguments). Up to 3 tries per variant;
   dropped variants are logged in `labels.json` → `dropped`.
4. **N0:** two deterministic cosmetic edits of the skill *body* per skill (a reviewer
   comment and whitespace; stock-phrase rewording). The frontmatter — name and trigger
   description — is never touched.
5. **D1:** the skill gains one blank line (the commit that triggers the check) while the world
   moves: one volatile value changes in a world-file override (EUR/USD, GBP/USD, a product
   version, a price, two package releases). The overlay cassettes are recomputed for every
   golden; Tavily fixtures report the new value. The goldens whose replay diverges become the
   case's affected pairs, expected `drift`.
6. **Tavily fixtures** (`bench/tavily_fixtures/`) report each golden fact's recorded value, so
   a drift check on a real regression finds the world unchanged. They are synthetic (every URL
   is `example.com`); `--live-tavily` re-queries the live API, which for a synthetic world
   finds nothing relevant (see limitations).

## Labels

`labels.json`, one entry per edit: `edit_id`, `skill`, `type`, `affected_goldens` (task ids
whose replay diverged in the manifestation check), `expected_decision` (BLOCK for R1–R6, PASS
for N0 and D1), `expected_pair_outcomes` (affected goldens → `regression`, or `drift` for D1;
every other golden is expected to `pass`), `change_summary` (the injector's one-line summary),
`source` (`llm`, `deterministic`, `hand`, `sim`), `manifested`, `attempts` and `review`.

**Human review:** 100% of controls and a seeded 20% of each regression type are
marked `review.required`. `ratchet bench label` shows the diff and the labels and records a
verdict (agree, or disagree with the corrected decision) and optional per-pair labels — the
subset Cohen's κ is computed on. Disagreements are resolved before the sweep: the reviewer's
decision is what the metrics score against. The report states how many required reviews are
done.

## Uses

Reporting Ratchet's detection recall per regression type, false-block and false-warn rates,
drift accuracy, the cost/recall frontier of tiered judging, the Level 0 ablation, and judge
agreement — always next to the E6 noise floor (the share of unchanged-skill replays judged
`pass`). Not intended as a general agent capability benchmark.

## Limitations (stated in the paper's abstract, not only here)

- **Synthetic tasks and world.** Everything is invented (the org `acme`, `example.com`,
  fictional products and packages). Real workloads are messier.
- **LLM-injected regressions.** R1–R6 edits are written by an LLM to a template, not observed
  in the wild; the manifestation check guarantees an observable change, not a realistic one.
- **Labels from construction.** Affected goldens come from one replay; nondeterminism can make
  a regression look absent (or an unchanged pair look changed). E6 measures that floor.
- **One harness, one agent tier.** Numbers come from MinimalRunner on Nemotron Super; its
  trigger matcher is lexical, which makes R5/R6 (trigger) cases depend on wording more than
  an embedding or Hermes matcher would. Results for Hermes need their own run.
- **Drift needs public facts.** The drift check re-grounds a fact with web search; for a
  synthetic world the fixtures stand in for the web. Live Tavily cannot confirm invented facts.
- **Paired ablations.** The judge-only configurations re-judge the cascade run's replays, so
  their differences isolate judging; they do not re-sample agent nondeterminism.

## Maintenance

Maintained with Ratchet, in this repository. A new dataset version re-records goldens
(`ratchet bench build --force`) and writes a new `bench/results/<date>/`. Simulator runs
(`bench/results/smoke-sim/`) test the pipeline and are never results (ADR 0018).
