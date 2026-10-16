# SkillRegress-Bench

SkillRegress-Bench is the labeled benchmark behind Ratchet's detection claims. Each case
pairs a skill edit with the blessed golden traces it affects and a ground-truth decision. The
benchmark answers three questions:

1. How many of the edits that break past tasks does the gate catch?
2. How often does it block edits that don't break anything?
3. What does judging cost?

World drift is a separate control, because a naive gate raises false alarms on it.

The data (skills, tasks, world, workspaces, edits, labels, fixtures, results) is CC BY 4.0.
The code (`ratchet/bench/`) is Apache-2.0. The full datasheet is `bench/DATASHEET.md`.

!!! danger "Simulated smoke output is not results"
    Only a `bench/results/<date>/` directory produced with live models
    (`RATCHET_UPSTREAM=live`) holds results. Its report header names the harness and the
    Nemotron model ids. `bench/results/smoke-sim/` comes from the offline simulator
    (ADR 0018), and every page of it says "SIMULATED MODELS — pipeline smoke test; not
    benchmark results". It proves that the pipeline and the report work. Never quote it.
    **The live pilot sweep ran on 2026-10-16**: `bench/results/2026-10-16/report.md`
    (35 labeled edits, real Nemotron Nano/Super/Ultra). The README's "SkillRegress-Bench
    results" section quotes it verbatim.

## Taxonomy

| Code | Name | What the edit does | Expected detector |
| --- | --- | --- | --- |
| R1 | Logic drift | Changes a step so the output is subtly wrong (wrong total, ordering, filter) | Cascade (Levels 1–3) |
| R2 | Dropped field | The output loses a required element (a VAT line, a due date, a recipient) | Cascade; Level 1 is often enough |
| R3 | Tool-argument change | Same tool, different arguments (wrong label, branch, calendar) | Level 0 changed args + cascade |
| R4 | Scope creep / unexpected side effect | A new write, send, spend or destructive call appears | Level 0 hard fail |
| R5 | Trigger over-firing | The description is broadened, so the skill fires on unrelated tasks | Trigger-neighborhood replays |
| R6 | Trigger under-firing | The description is narrowed, so the skill stops firing on its own tasks | Direct replays: missing calls, degraded output |
| D1 | World drift (control) | Skill unchanged in substance; the world moved (a volatile fact has a new value) | Drift check: must be `drift`, not a regression. Expected PASS |
| N0 | No-op / cosmetic (control) | Whitespace, a comment, stock-phrase rewording | Must PASS. Measures false blocks |

## Composition

| Part | Count |
| --- | --- |
| Skills | 10 agentskills.io skills in `bench/skills/` |
| Tasks | 100 (10 per skill, including 2 edge cases each) + 3 skill-free canaries, in `bench/tasks.yaml` |
| Goldens | 103 recorded and blessed traces, one per task |
| Regression edits | up to 120: 10 skills × R1–R6 × 2 variants, injected by an LLM and checked to manifest |
| N0 controls | 20 (2 per skill), deterministic cosmetic edits of the skill body |
| D1 controls | 6 (2 per world-dependent skill), with a world overlay and Tavily fixtures |
| Configurations | 7: `cascade`, `nano_only`, `super_only`, `ultra_only`, `drift_off`, `level0_off`, `noise` |

The skills are expense-report, github-triage, calendar-scheduler, changelog-writer,
csv-cleaner, invoice-parser, weekly-research-brief, fx-expense-converter,
dependency-updater and meeting-notes. Three of them are world-dependent:
weekly-research-brief, fx-expense-converter and dependency-updater. Every tool is mocked, so
the benchmark never causes a live side effect.

## How it is built

1. **Tasks** are hand-written, with varied phrasing and 2 edge cases per skill (a missing
   file, an empty input, a weekend rate date, …). `ratchet bench validate` checks that every
   task routes to its skill.
2. **Goldens:** each task runs once on the demo agent (MinimalRunner → capture proxy →
   Nemotron Super, mock tools in record mode, frozen at 2026-10-14T16:00:00Z, seed = hash of
   the task id) and is blessed.
3. **Regressions:** Nemotron Ultra edits each skill to inject one R type
   (`inject_regression.md`). A variant is kept only if it is a valid skill with the same name
   and it **manifests**: replaying it makes at least one golden diverge. Each variant gets up
   to 3 tries; dropped variants are logged.
4. **N0:** two deterministic cosmetic edits per skill. The frontmatter (name and trigger
   description) is never touched.
5. **D1:** the skill gains one blank line (the commit that triggers the check), while one
   volatile value moves in a world-file override. Tavily fixtures report the new value.
6. **Labels** (`bench/labels/labels.json`) record the affected goldens, the expected decision
   (BLOCK for R1–R6, PASS for N0 and D1) and the expected pair outcomes.
7. **Human review:** 100 % of controls and a seeded 20 % of each R type are marked for
   review. `ratchet bench label` shows the diff and the labels and records a verdict. Where
   a reviewer disagrees, the reviewer's decision is what the metrics score against.

## Experiments and metrics

| ID | Question | Configurations |
| --- | --- | --- |
| E1 | How well does the full cascade detect each regression type? | `cascade` |
| E2 | How often does it block things it shouldn't? | `cascade` on N0 + D1 |
| E3 | Does Tavily drift disambiguation work? | `cascade` vs `drift_off` |
| E4 | What does tiering buy? | `nano_only`, `super_only`, `ultra_only`, `cascade` |
| E5 | Is Level 0 doing real work? | `cascade` vs `level0_off` |
| E6 | How much nondeterminism is there? | `noise`: replay goldens with the *unchanged* skill |

| Metric | Definition |
| --- | --- |
| Detection recall | share of R1–R6 edits decided BLOCK or WARN, per type and overall |
| Block recall | share of R1–R6 edits decided BLOCK |
| False-block rate | share of N0 and D1 edits decided BLOCK |
| False-warn rate | share of N0 edits decided WARN |
| Drift accuracy | share of D1 edits that passed with every affected pair that differed judged `drift` (a labeled pair whose replay happened not to differ is not a miss) |
| Cost per verdict | USD by tier, mean and p95, per configuration |
| Latency | edit-to-verdict wall time, mean and p95 |
| Judge agreement | Cohen's κ between each tier's opinions and the human labels |
| E6 noise floor | share of unchanged-skill replays judged `pass` |

## Commands

```bash
uv run ratchet bench validate                  # skills, world, workspaces, trigger routing
uv run ratchet bench smoke                     # simulator only: build + every config → results/smoke-sim/
uv run ratchet bench build [--skills a,b] [--types R2,R4] [--no-goldens] [--no-controls] [--no-inject] [--yes]
uv run ratchet bench label --status            # the review queue
uv run ratchet bench label [<edit_id>] [--agree | --disagree --decision PASS] [--pair task_id=regression]
uv run ratchet bench run --config bench/configs/<name>.yaml --out bench/results/<date>/ \
                         [--limit N] [--types R1,N0] [--seed 0] [--live-tavily] [--concurrency 2] [--yes]
uv run ratchet bench report bench/results/<date>/   # re-render report.md (no model calls)
uv run ratchet bench compare bench/results/<a> bench/results/<b>
```

Every option is in the [CLI reference](cli.md#ratchet-bench). Some behaviors to know:

- **Its own work area.** The bench keeps its own store and skills repo in `var/bench/`
  (`--work`), separate from your data. Every edit runs on a scratch clone, so the bench
  repo's `main` never moves.
- **Resumable.** An edit already in `results.json` for that configuration is skipped, so a
  crash or a cap stop resumes where it left off.
- **Paired samples.** `--limit N` takes a seeded sample stratified by type. The same `--seed`
  gives the same subset in every configuration, so the configurations stay paired.
- **Replay reuse.** `nano_only`, `super_only`, `ultra_only`, `drift_off` and `level0_off`
  re-judge the `cascade` run's replays in the same results directory, with no second round
  of agent spend. Run `cascade` first.

### The live run

```bash
export RATCHET_UPSTREAM=live            # NEBIUS_TOKEN_FACTORY_KEY set; TAVILY_API_KEY optional
DATE=$(date -u +%F)
uv run ratchet bench validate
uv run ratchet bench build --no-controls --no-inject --yes     # 103 goldens
uv run ratchet bench build --no-goldens --yes                  # N0, D1, then R1–R6 injection
uv run ratchet bench label                                     # human review, before the sweep
uv run ratchet bench run --config bench/configs/noise.yaml   --out bench/results/$DATE/ --limit 10 --yes
uv run ratchet bench run --config bench/configs/cascade.yaml --out bench/results/$DATE/ --limit 40 --yes
uv run ratchet bench run --config bench/configs/nano_only.yaml  --out bench/results/$DATE/ --limit 40 --yes
uv run ratchet bench run --config bench/configs/super_only.yaml --out bench/results/$DATE/ --limit 40 --yes
uv run ratchet bench run --config bench/configs/ultra_only.yaml --out bench/results/$DATE/ --limit 10 --yes
uv run ratchet bench run --config bench/configs/drift_off.yaml  --out bench/results/$DATE/ --limit 40 --yes
uv run ratchet bench run --config bench/configs/level0_off.yaml --out bench/results/$DATE/ --limit 40 --yes
```

Run E6 (`noise`) first: its result can change thresholds before the main sweep.

## Costs

Every `build` and `run` prints its estimate before it starts. On live models it refuses to
start when the estimate exceeds what is left of `RATCHET_BENCH_USD_CAP` ($12), it needs
`--yes` (or an interactive yes) to spend anything, and it stops scheduling once the cap is
reached. Injection calls also carry the proxy's `bench` purpose and its hard cap.
`ratchet bench smoke` always runs on the simulator.

Judge tokens follow fixed per-call budgets (Nano screen 8k in / 0.3k out, Super check
12k / 1k, Ultra adjudicate 15k / 2k). The agent replay is the main unknown: about
$0.012 per replay on its 25k in / 5k out budget, and about $0.003 by the simulator's count on this
dataset. Once goldens are recorded live, the runner uses their observed mean instead.

| Step | Full scale, at $0.012 / $0.003 per replay | Suggested `--limit` | At that limit |
| --- | --- | --- | --- |
| build: 103 goldens + bless | $1.39 / $0.46 | — | — |
| build: 120 injections + manifestation replays + 6 D1 | $30.8 / $8.9 | `--skills` with 5 skills ≈ half | $15.4 / $4.5 |
| noise (E6) | $7.75 / $3.70 | 10 | $2.58 / $1.23 |
| cascade (E1, E2, E4, E3/E5 on) | $37.72 / $18.01 | 40 | $10.33 / $4.93 |
| nano_only (E4) | $3.88 | 40 | $1.06 |
| super_only (E4) | $12.53 | 40 | $3.43 |
| ultra_only (E4) | $48.66 | 10 | $3.33 |
| drift_off (E3) | $9.82 | 40 | $2.69 |
| level0_off (E5) | $11.44 | 40 | $3.13 |

The full-scale sweep does not fit the $12 cap. Within a strict $12, the plan is noise (10),
cascade (40), nano_only (40) and super_only (40), about $10.7, then `ultra_only --limit 10`
if the guard allows. Identical judge prompts hit the proxy's response cache, so the actual
spend of judge-only configurations is usually below the estimate. Cost-per-verdict metrics
price the opinion tokens at catalog prices, so they don't depend on the cache.

## How to read `report.md`

A results directory holds `results.json`, `report.md` and `cost_vs_recall.svg`. The report
has these sections:

1. **Header.** Harness, models (or the SIMULATED banner), date, git sha, the labels file with
   its per-type counts, how many required human reviews are done, and the E6 noise floor.
   Check the models row first: if it says SIMULATED, stop reading for results.
2. **Produced by.** The exact command line for each configuration.
3. **Table 2: detection, false blocks and cost per configuration** (E1, E2, E4). Recall per
   R type and overall, block recall, false blocks on N0 and D1, drift accuracy, cost per
   verdict (mean and p95), the all-Ultra cost, and the noise floor. Every cell links to the
   config that produced it.
   Under the table, the **prompt-cache share**: the share of prompt tokens the provider served
   from its prompt cache, over every edit in the results, overall and per tier. Results
   written before this field existed say "not recorded".
4. **Figure 1: cost per verdict vs detection recall** (E4), one point per configuration.
5. **E3: drift on vs off.** **E5: Level 0 on vs off.** **E6: noise floor.**
6. **Pair level, false warns and latency.**
7. **Judge agreement:** Cohen's κ per tier against the human labels.
8. **Worked examples:** the diff, the pair evidence and the summary, for a sample of types.
9. **Taxonomy.**

Read every recall figure against the E6 noise floor next to it. Each section names the
function in `ratchet/bench/metrics.py` that computed it. Recall counts BLOCK or WARN; block
recall counts BLOCK only.

## Limitations

- **Synthetic tasks and world.** Everything is invented (the org `acme`, `example.com`,
  fictional products and packages). Real workloads are messier.
- **LLM-injected regressions.** R1–R6 edits are written by an LLM to a template, not observed
  in the wild. The manifestation check guarantees an observable change, not a realistic one.
- **Labels from construction.** Affected goldens come from one replay, and nondeterminism can
  hide a regression or fake a change. E6 measures that floor.
- **One harness, one agent tier.** Numbers come from the MinimalRunner on Nemotron Super. Its
  lexical trigger matcher makes R5/R6 depend on wording more than an embedding or Hermes
  matcher would.
- **Drift needs public facts.** For a synthetic world, the fixtures stand in for the web.
  Live Tavily can't confirm invented facts.
- **Paired ablations.** Judge-only configurations re-judge the cascade run's replays. Their
  differences isolate judging; they don't re-sample agent nondeterminism.
