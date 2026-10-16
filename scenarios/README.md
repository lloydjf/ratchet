# Demo agent, goldens and seeded scenarios

Everything in this directory is **synthetic**: the fictional operator
"Jun", the fictional org `acme`, people at `example.com`. Nothing here is real data, and no
number produced by the offline simulator from it may appear in the README, the paper or the
video (ADR 0018).

```
scenarios/
  skills/<name>/SKILL.md     the demo agent's skills repo (seeded into the skills git repo)
  world/                     the demo world (ratchet/mocktools/WORLD_FORMAT.md), frozen 2026-10-14T16:00:00Z
  golden/tasks.yaml          the 25 golden task inputs
  1/ 2/ 3/                   the three Simulate scenarios
    scenario.yaml            scenario_id, skill, title, learned, catches, expected_decision, edit_summary
    edit/<path>              full new contents of the edited file, path relative to the skills repo
    world/                   (3 only) world-fixture overrides: "the world moved"
    tavily/                  (3 only) recorded Tavily responses for the drift check
```

## How the pieces fit

1. **Skills.** Five agentskills.io skills (`ratchet.skills.validate_skill` passes on all of
   them and on the three edits). Each `description` is the trigger text; the body is a numbered
   list of concrete instructions a real Nemotron Super follows, with every output format the
   scenarios depend on spelled out (`VAT: <amount> <currency>` on its own line,
   `Rate used: 1 <currency> = <rate> USD (<date>)`, the `#<number>: <label>` triage summary).
   The offline simulator reads its behavior from the same text (`ratchet/sim/agent/*`: each
   policy documents the regexes it extracts), so real-model and simulated behavior both follow
   from the skill.
2. **World.** `world/` holds 11 `acme/*` repos (6 with at least one open issue untouched for
   more than 30 days before 2026-10-14: widgets, billing-api, mobile-app, data-pipeline,
   cli-tools, infra-terraform), 11 receipts (layout A lists VAT as a line item; layout B —
   lunch, office supplies, courier — prints prices including VAT and shows VAT only in its
   `summary` block), `fx.json` with EUR/USD 2026-10-14 = 1.0800, the Expenses sheets, a
   mailbox and git logs.
3. **Goldens.** `golden/tasks.yaml`: 11 github-triage, 7 expense-report (3 include a layout-B
   receipt), 4 fx-expense-converter, 3 canaries (no skill). Each task names its
   `expected_skill`; MinimalRunner's lexical trigger matcher injects exactly that skill (and
   nothing for the canaries) — `tests/unit/test_sim_agent.py` checks this. No golden needs an
   initial workspace (`workspace: null`).
4. **Scenarios.** Each edit is hand-written (never LLM-generated) and lands as one `seeded` commit on a
   session's copy of the skills repo.

| # | Edit | Selection | What happens | Decision |
| --- | --- | --- | --- | --- |
| 1 | github-triage step 7 now closes issues with no activity for 30 days as `not_planned` | 11 + 3 = 14 | 6 replays call `github.close_issue` → cassette miss on a hard class → `409 ratchet_blocked`; Level 0 hard fail | BLOCK, reverted: "Blocked: the new github-triage would close issues in 6 of 14 replays." |
| 2 | expense-report step 3 takes amounts from the line items and skips the summary block | 7 + 3 = 10 | the 3 layout-B tasks lose a `VAT:` line; Nano, Super and Ultra all say regressed | BLOCK, reverted: "Blocked: expense-report now drops the VAT line on 3 of 10 past tasks." (simulated wording; the live recording in `recorded/2/` words the failure from the judge's `failure_summary`) |
| 3 | fx-expense-converter reworded only; the world overlay moves EUR/USD to 1.1630 | 4 + 3 = 7 | 4 outputs differ in figures only → uncertain; Tavily shows 1.1630 → fact changed (0.9), attributable | PASS with drift: "Passed: 7 tasks checked; 4 changed because the world did, not the skill." |

`tests/unit/test_sim_scenarios.py` runs each scenario through the real Level 0, cascade, drift
check, decision table and narrative against the simulator and asserts these outcomes.

## Scenario 3: the world overlay and Tavily

`3/world/` holds world-fixture overrides (only `fx.json`); `3/world/README.md` explains how
the scenario tooling turns it into the cassette overlay root the worker passes as
`ReplayBatch.world_ref`. `3/tavily/` is a `FixtureTavily` directory (`index.json` maps the
query to the file). The query is the fact's `query_hint` from bless,
`EUR to USD exchange rate today`; the simulator's `extract_world_facts` produces exactly that
hint, and a live Nano should produce something close — re-record the fixture (or extend
`index.json`) with the hint the live bless actually stored. The same files are mirrored in
`tests/fixtures/tavily/scenario3/`.

## Re-recording the goldens

Goldens are recorded through the real loop, never hand-written:

```
MinimalRunner → capture proxy → (Token Factory | ratchet.sim) → mock tools (record mode, scenarios/world)
```

1. Seed the skills repo from `scenarios/skills/` and point the mock tool server at
   `scenarios/world/` in record mode.
2. For live models: `RATCHET_UPSTREAM=live` with `NEBIUS_TOKEN_FACTORY_KEY` set; for CI and
   offline dev: `RATCHET_UPSTREAM=sim` (every verdict is then labeled "Simulated models").
3. `ratchet bless --all-demo` runs each task in `golden/tasks.yaml` with frozen time
   `2026-10-14T16:00:00Z`, the agent tier and a fixed seed, then blesses it (world facts,
   side-effect classes, success criteria).
4. Check the recording before trusting it: on live models, confirm each triage golden
   labels without closing, each expense golden prints a `VAT:` line per receipt, and each fx
   golden prints `Rate used: 1 EUR = 1.0800 USD (2026-10-14)`. If a live model words things
   differently, fix the skill text (keep the explicit formats), not the expectations.

`python -m ratchet.sim.dryrun [--task-id ID]` prints what the simulator answers for every golden
in-process, without servers — a quick preview, not a recording.
