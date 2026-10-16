# SkillRegress-Bench

The evidence behind Ratchet's README numbers: 10 skills × 10 golden tasks, LLM-injected
regressions R1–R6, N0 cosmetic and D1 world-drift controls, a runner that pushes every edit
through Ratchet's real replay pipeline, and a report with recall, false blocks, drift accuracy, cost
per verdict, latency, judge agreement and the E6 noise floor. Construction, composition,
labeling and limitations: [`DATASHEET.md`](DATASHEET.md). Data CC BY 4.0, code Apache-2.0.

> **Which numbers are results.** Only a `bench/results/<date>/` produced with live models
> (`RATCHET_UPSTREAM=live`) is a result; its header names the harness and the Nemotron model
> ids. [`results/smoke-sim/`](results/smoke-sim/report.md) is produced by the offline simulator
> (ADR 0018) and is labeled "SIMULATED MODELS — pipeline smoke test; not benchmark results"
> on every page: it proves the pipeline works and must never be quoted.

## Layout

| Path | What |
| --- | --- |
| `skills/` | the 10 skills (agentskills.io; `ratchet bench validate` checks them) |
| `world/`, `workspaces/` | the synthetic world served by the mock tools, and initial task workspaces |
| `tasks.yaml` | 100 tasks (10 per skill, 2 edge cases each) + 3 canaries; `smoke: true` marks the simulator subset |
| `configs/` | `cascade` (E1, E2, E3/E5 on, E4), `nano_only` / `super_only` / `ultra_only` (E4), `drift_off` (E3), `level0_off` (E5), `noise` (E6) |
| `edits/`, `labels/labels.json` | labeled edits, written by `ratchet bench build` |
| `tavily_fixtures/` | recorded (synthetic) Tavily responses for the goldens' facts |
| `smoke/` | hand-written edits for the simulator smoke test |
| `results/<date>/` | `results.json`, `report.md`, `cost_vs_recall.svg` |

The bench keeps its own store and skills repo in `var/bench/` (`--work`), separate from the
operator's data; every edit runs on a scratch clone, so the bench repo's `main` never moves.

## Commands

The sub-app is registered in the main CLI (`ratchet bench …`; from a checkout,
`uv run ratchet bench …`).

```bash
ratchet bench validate                      # skills, world, workspaces, trigger routing
ratchet bench smoke                         # simulator only: build + every config → results/smoke-sim/
ratchet bench build [--skills a,b] [--types R2,R4] [--no-goldens] [--no-controls] [--no-inject] [--yes]
ratchet bench label --status                # the review queue (100% of controls, 20% of each R type)
ratchet bench label [<edit_id>] [--agree | --disagree --decision PASS] [--pair task_id=regression]
ratchet bench run --config bench/configs/<name>.yaml --out bench/results/<date>/ \
                  [--limit N] [--types R1,N0] [--seed 0] [--live-tavily] [--concurrency 2] [--yes]
ratchet bench report bench/results/<date>/  # re-render report.md from results.json
ratchet bench compare bench/results/<a> bench/results/<b>
```

- **Resumable:** an edit already in `results.json` for that config is skipped; a crash or a
  cap stop resumes where it left off.
- **`--limit N`** takes a seeded sample stratified by type (the same `--seed` gives the same
  subset in every config, so configs stay paired).
- **Replay reuse:** `nano_only`, `super_only`, `ultra_only`, `drift_off` and `level0_off`
  re-judge the `cascade` run's replays in the same results directory (no second round of agent
  spend; differences isolate judging). Run `cascade` first. Identical judge prompts also hit the
  proxy's response cache, so their actual spend is below the estimate; cost-per-verdict metrics
  price opinion tokens at the configured list prices (`ratchet/config/models.py`) and are
  cache-independent.
- **Budget guard:** every `build` and `run` prints its estimate first. On live models it
  refuses to start when the estimate exceeds what is left of `RATCHET_BENCH_USD_CAP` ($12) for
  that phase, needs `--yes` (or an interactive yes) to spend anything, and stops scheduling
  once the cap is reached. Construction (goldens, bless, injection, manifestation replays) and
  the sweep are capped separately; injection calls also carry the
  proxy's `bench` purpose and its hard cap.
- **The bench cap is all-time:** the proxy refuses a `bench`-purpose call once the bench
  store's *cumulative* `bench` spend reaches `RATCHET_BENCH_USD_CAP` (it is not a daily cap
  and does not reset at midnight). `build` and `run` print
  `bench spend so far (all-time): $X of $CAP` before they start.

## The live run (needs keys; run deliberately)

```bash
export RATCHET_UPSTREAM=live            # NEBIUS_TOKEN_FACTORY_KEY set; TAVILY_API_KEY optional
DATE=$(date -u +%F)
ratchet bench validate
ratchet bench build --no-controls --no-inject --yes          # 103 goldens
ratchet bench build --no-goldens --yes                       # N0, D1, then R1–R6 injection
ratchet bench label                                          # human review, before the sweep
ratchet bench run --config bench/configs/noise.yaml   --out bench/results/$DATE/ --limit 10 --yes   # E6 first: it may change thresholds
ratchet bench run --config bench/configs/cascade.yaml --out bench/results/$DATE/ --limit 40 --yes
ratchet bench run --config bench/configs/nano_only.yaml  --out bench/results/$DATE/ --limit 40 --yes
ratchet bench run --config bench/configs/super_only.yaml --out bench/results/$DATE/ --limit 40 --yes
ratchet bench run --config bench/configs/ultra_only.yaml --out bench/results/$DATE/ --limit 10 --yes
ratchet bench run --config bench/configs/drift_off.yaml  --out bench/results/$DATE/ --limit 40 --yes
ratchet bench run --config bench/configs/level0_off.yaml --out bench/results/$DATE/ --limit 40 --yes
```

### Cost estimates (printed again, with observed figures, before every step)

Judge tokens are the budgeted figures (screen 8k/0.3k Nano; check 12k/1k Super; adjudicate
15k/2k Ultra; assumed escalation 1 : 0.30 : 0.10; ~15 pairs per edit = 10 direct + 3 canaries +
~2 neighbors). The agent replay is the dominant unknown: the budget assumes 25k in / 5k out on
Super (**$0.012**); the simulator's usage, which counts the real prompts at 4 chars per token,
measures about **$0.003** per replay on this dataset. Once goldens are recorded live, the runner
uses their observed mean instead.

| Step | Full scale (146 edits; noise 30) at $0.012 / $0.003 per replay | Suggested `--limit` | at $0.012 / $0.003 |
| --- | --- | --- | --- |
| build: 103 goldens + bless | $1.39 / $0.46 | — | — |
| build: 120 injections + manifestation replays + 6 D1 | $30.8 / $8.9 | `--skills` 5 skills ≈ half | $15.4 / $4.5 |
| noise (E6) | $7.75 / $3.70 | 10 (one repeat per skill) | $2.58 / $1.23 |
| cascade (E1, E2, E4, E3/E5 on) | $37.72 / $18.01 | 40 | $10.33 / $4.93 |
| nano_only (E4) | $3.88 | 40 | $1.06 |
| super_only (E4) | $12.53 | 40 | $3.43 |
| ultra_only (E4) | $48.66 | 10 | $3.33 |
| drift_off (E3) | $9.82 | 40 | $2.69 |
| level0_off (E5) | $11.44 | 40 | $3.13 |

The full-scale sweep does not fit the $12 sweep cap at either replay cost; the suggested limits
total about $20 at $0.003 per replay (judge-only configs are mostly cache hits in practice).
Within a strict $12: noise (10) + cascade (40) + nano_only (40) + super_only (40) ≈ $10.7, then
`ultra_only --limit 10` if the guard allows. Raising `RATCHET_BENCH_USD_CAP` is the operator's call.

## Which harness and models produced which numbers

| Results | Harness | Models | Status |
| --- | --- | --- | --- |
| [`results/smoke-sim/`](results/smoke-sim/report.md) | MinimalRunner | offline simulator (`ratchet-sim/*`) | **SIMULATED — pipeline smoke test, not results** |
| [`results/2026-10-16/`](results/2026-10-16/report.md) (live) | MinimalRunner (`ratchet_harness=minimal`) | Nemotron Nano / Super / Ultra via Token Factory (ids in the report header) | **Live pilot sweep** — 35 labeled edits, `--limit 40`, noise `--limit 8`; about $21 of model spend |

Every number in a `report.md` links to the config that produced it, its section names the
function in `ratchet/bench/metrics.py` that computed it, and the E6 noise floor sits next to
every recall figure.

## Tests

`uv run pytest tests -q -k bench` (unit: metrics incl. Cohen's κ against the textbook example,
labels, controls, dataset, runner planning, report golden file; integration: controls through
the replay pipeline with replay reuse and resume). The full simulator smoke is
`uv run pytest tests/integration/test_bench_smoke.py -m e2e` (~3 min).
