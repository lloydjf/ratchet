# SkillRegress-Bench results — 2026-10-16

> **SIMULATED MODELS — pipeline smoke test; not benchmark results.** Every number below comes from the offline
> simulator (ADR 0018), not from Nemotron models; it shows that the pipeline and the report work, and must never be quoted as a result.

| | |
| --- | --- |
| Harness | `minimal` (agent tier `super`) |
| Models | SIMULATED — smoke test, not results |
| Date | 2026-10-16 (results updated 2026-10-16 12:00 UTC) |
| Git sha | `abc1234` |
| Labels | `bench/results/x/labels.json` — 9 edits (N0 2, R2 1) |
| Human review | 1 of 3 required reviews recorded |
| E6 noise floor | 0.75 pass rate (4 unchanged-skill pairs) with control replays; 0.00 of pairs diverged on the control replay |

Produced by:

- `cascade` (E1, E4): `ratchet bench run --config bench/configs/cascade.yaml --out bench/results/x`
- `nano_only` (E4): `ratchet bench run --config bench/configs/nano_only.yaml --out bench/results/x`
- `noise` (E6): `ratchet bench run --config bench/configs/noise.yaml --out bench/results/x`

## Table 2 — detection, false blocks and cost per configuration (E1, E2, E4)

Computed by `summarize` in [`metrics.py`](../../../ratchet/bench/metrics.py). Recall counts BLOCK or WARN; read every recall figure against the E6 noise floor in the last column.

| config | R1 recall | R2 recall | R3 recall | R4 recall | R5 recall | R6 recall | recall | block recall | false-block N0 | false-block D1 | drift acc. | cost/verdict mean / p95 | all-Ultra mean | E6 noise floor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `nano_only` | [1.00 (1/1)](../../configs/nano_only.yaml) | [1.00 (1/1)](../../configs/nano_only.yaml) | [n/a (0)](../../configs/nano_only.yaml) | [1.00 (1/1)](../../configs/nano_only.yaml) | [0.00 (0/1)](../../configs/nano_only.yaml) | [n/a (0)](../../configs/nano_only.yaml) | [0.75 (3/4)](../../configs/nano_only.yaml) | [0.50 (2/4)](../../configs/nano_only.yaml) | [0.00 (0/2)](../../configs/nano_only.yaml) | [0.50 (1/2)](../../configs/nano_only.yaml) | [0.50 (1/2)](../../configs/nano_only.yaml) | [$0.00020 / $0.00020](../../configs/nano_only.yaml) | [$0.00394](../../configs/nano_only.yaml) | [0.75 pass rate (4 unchanged-skill pairs) with control replays; 0.00 of pairs diverged on the control replay](../../configs/nano_only.yaml) |
| `cascade` | [1.00 (1/1)](../../configs/cascade.yaml) | [1.00 (1/1)](../../configs/cascade.yaml) | [n/a (0)](../../configs/cascade.yaml) | [1.00 (1/1)](../../configs/cascade.yaml) | [0.00 (0/1)](../../configs/cascade.yaml) | [n/a (0)](../../configs/cascade.yaml) | [0.75 (3/4)](../../configs/cascade.yaml) | [0.50 (2/4)](../../configs/cascade.yaml) | [0.00 (0/2)](../../configs/cascade.yaml) | [0.50 (1/2)](../../configs/cascade.yaml) | [0.50 (1/2)](../../configs/cascade.yaml) | [$0.00131 / $0.00295](../../configs/cascade.yaml) | [$0.00394](../../configs/cascade.yaml) | [0.75 pass rate (4 unchanged-skill pairs) with control replays; 0.00 of pairs diverged on the control replay](../../configs/cascade.yaml) |

Prompt-cache share: 0.32 of prompt tokens were cache hits (per tier: nano 0.00, super 0.50, ultra 0.25).

## Figure 1 — cost per verdict vs detection recall (E4)

![Cost per verdict vs detection recall: nano_only $0.00020 → recall 0.75; cascade $0.00131 → recall 0.75](cost_vs_recall.svg)

## E3 — drift disambiguation on vs off

| config | drift acc. | false-block D1 | false-block | recall |
| --- | --- | --- | --- | --- |
| `cascade` | [0.50 (1/2)](../../configs/cascade.yaml) | [0.50 (1/2)](../../configs/cascade.yaml) | [0.25 (1/4)](../../configs/cascade.yaml) | [0.75 (3/4)](../../configs/cascade.yaml) |

## E5 — Level 0 on vs off

| config | R3 recall | R4 recall | recall | cost/verdict mean / p95 |
| --- | --- | --- | --- | --- |
| `cascade` | [n/a (0)](../../configs/cascade.yaml) | [1.00 (1/1)](../../configs/cascade.yaml) | [0.75 (3/4)](../../configs/cascade.yaml) | [$0.00131 / $0.00295](../../configs/cascade.yaml) |

## E6 — noise floor (unchanged skill, replayed)

[0.75 pass rate (4 unchanged-skill pairs) with control replays; 0.00 of pairs diverged on the control replay](../../configs/noise.yaml); outcomes: pass 3, uncertain 1; 2 edits over 1 repeat(s) (`noise_floor` in [`metrics.py`](../../../ratchet/bench/metrics.py)). A pass rate below 1.0 is judge or agent nondeterminism on edits that changed nothing: it bounds what any recall number above can mean.

## Pair level, false warns and latency

| config | pair precision | pair recall | false-warn N0 | latency mean / p95 | errors |
| --- | --- | --- | --- | --- | --- |
| `cascade` | [0.60 (3/5)](../../configs/cascade.yaml) | [0.75 (3/4)](../../configs/cascade.yaml) | [0.50 (1/2)](../../configs/cascade.yaml) | [10.0 s / 10.0 s](../../configs/cascade.yaml) | [1/9](../../configs/cascade.yaml) |
| `nano_only` | [0.60 (3/5)](../../configs/nano_only.yaml) | [0.75 (3/4)](../../configs/nano_only.yaml) | [0.50 (1/2)](../../configs/nano_only.yaml) | [10.0 s / 10.0 s](../../configs/nano_only.yaml) | [1/9](../../configs/nano_only.yaml) |

## Judge agreement — Cohen's κ per tier

Binary per pair (`regressed`/`uncertain` = flag), `kappa_by_tier` in [`metrics.py`](../../../ratchet/bench/metrics.py). Construction labels are the manifestation-checked `expected_pair_outcomes`; human labels are the reviewed pair subset.

| config | nano vs construction | super vs construction | ultra vs construction | nano vs human | super vs human | ultra vs human |
| --- | --- | --- | --- | --- | --- | --- |
| `cascade` | [0.06 (n=8)](../../configs/cascade.yaml) | [0.00 (n=2)](../../configs/cascade.yaml) | [0.00 (n=2)](../../configs/cascade.yaml) | [1.00 (n=2)](../../configs/cascade.yaml) | [n/a (n=1)](../../configs/cascade.yaml) | [n/a (n=1)](../../configs/cascade.yaml) |
| `nano_only` | [0.06 (n=8)](../../configs/nano_only.yaml) | [0.00 (n=2)](../../configs/nano_only.yaml) | [0.00 (n=2)](../../configs/nano_only.yaml) | [1.00 (n=2)](../../configs/nano_only.yaml) | [n/a (n=1)](../../configs/nano_only.yaml) | [n/a (n=1)](../../configs/nano_only.yaml) |

## Worked examples

From the `cascade` run.

### R2 — Dropped field: `r2-a`

Skill `expense-report`. Expected **BLOCK**, got **[BLOCK](../../configs/cascade.yaml)** (correct); reasons `agreed_regression`; judge cost [$0.00100](../../configs/cascade.yaml).

```diff
--- a/expense-report/SKILL.md
+++ b/expense-report/SKILL.md
@@ -1 +1 @@
-old
+new
```

| golden task | outcome | expected | decided by | opinions | Level 0 | drift |
| --- | --- | --- | --- | --- | --- | --- |
| t1 | regression | regression | ultra | nano regressed 0.93 → super regressed 0.90 → ultra regressed 0.90 | — | — |
| t2 | pass | pass | nano | nano equivalent 0.95 | — | — |

> Narrative for r2-a.

### R4 — Scope creep / unexpected side effect: `r4-a`

Skill `expense-report`. Expected **BLOCK**, got **[BLOCK](../../configs/cascade.yaml)** (correct); reasons `agreed_regression`; judge cost [$0.00100](../../configs/cascade.yaml).

(no diff recorded)

| golden task | outcome | expected | decided by | opinions | Level 0 | drift |
| --- | --- | --- | --- | --- | --- | --- |
| t3 | regression | regression | level0 | — | — | — |

> Narrative for r4-a.

### R5 — Trigger over-firing: `r5-a`

Skill `expense-report`. Expected **BLOCK**, got **[PASS](../../configs/cascade.yaml)** (incorrect); reasons `all_clear`; judge cost [$0.00400](../../configs/cascade.yaml).

(no diff recorded)

| golden task | outcome | expected | decided by | opinions | Level 0 | drift |
| --- | --- | --- | --- | --- | --- | --- |
| t4 | pass | regression | nano | nano equivalent 0.95 | — | — |

> Narrative for r5-a.

### D1 — World drift (control): `d1-a`

Skill `expense-report`. Expected **PASS**, got **[PASS](../../configs/cascade.yaml)** (correct); reasons `all_clear`; judge cost [$0.00100](../../configs/cascade.yaml).

(no diff recorded)

| golden task | outcome | expected | decided by | opinions | Level 0 | drift |
| --- | --- | --- | --- | --- | --- | --- |
| t6 | drift | drift | nano | nano regressed 0.93 | — | — |

> Narrative for d1-a.

### N0 — No-op / cosmetic (control): `n0-a`

Skill `expense-report`. Expected **PASS**, got **[PASS](../../configs/cascade.yaml)** (correct); reasons `all_clear`; judge cost [$0.00050](../../configs/cascade.yaml).

(no diff recorded)

| golden task | outcome | expected | decided by | opinions | Level 0 | drift |
| --- | --- | --- | --- | --- | --- | --- |
| t1 | pass | pass | nano | nano equivalent 0.95 | — | — |

> Narrative for n0-a.

## Taxonomy

| code | name |
| --- | --- |
| R1 | Logic drift |
| R2 | Dropped field |
| R3 | Tool-argument change |
| R4 | Scope creep / unexpected side effect |
| R5 | Trigger over-firing |
| R6 | Trigger under-firing |
| D1 | World drift (control) |
| N0 | No-op / cosmetic (control) |
