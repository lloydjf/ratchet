# FAQ

## Do I need API keys to try it?

No. Without keys, everything runs against the offline simulator (`RATCHET_UPSTREAM=sim`):
seeding, the three scenarios, the dashboard and the benchmark smoke test. Every verdict it
produces says **Simulated models**. For real Nemotron models, set
`NEBIUS_TOKEN_FACTORY_KEY` and `RATCHET_UPSTREAM=live`. See
[Get started](getting-started.md#2-choose-simulated-or-live-models).

## How much does a verdict cost on real models?

On the three seeded scenarios, judging cost $0.014–$0.039 per verdict, and the agent replays
cost $0.020–$0.091 on Super. These scenarios were built to be hard, so many of their pairs
are contested on purpose. On a typical edit, most pairs end at Level 0 or Nano. The cost
panel on every verdict shows the actual cost by tier next to the all-Ultra counterfactual.
The daily cap defaults to $10.

## How long does a verdict take?

On real models, the seeded scenarios took 41–72 seconds from skill edit to verdict, on the
local runner with the response cache off; the PASS-with-drift scenario is the slow one because
every drifted task costs a live Tavily check and an Ultra adjudication. On the simulator, they take a few seconds. The design target for a 20-trace
edit on Nebius Serverless Jobs is under 4 minutes.

## Why did my edit get WARN instead of BLOCK?

A BLOCK needs either a Level 0 hard fail (a new irreversible, destructive, send or spend
call) or Super and Ultra agreeing that a task regressed. If only one judge found a
regression, the judges disagreed, a replay failed, or a judge was unsure, Ratchet holds the
edit for you instead (ADR 0007). The header notice and the pair grid say which. See
[Decisions and actions](concepts.md#decisions-and-actions).

## Ratchet blocked an edit I wanted. What now?

Open the verdict. If the evidence is wrong, or the change is intended (the skill *should*
now close stale issues), press **Approve anyway**. Ratchet re-applies the reverted edit and
merges it into `main`, and Rollback stays available. From the CLI:
`uv run ratchet approve <verdict_id>`.

## Can an earlier edit block a later one?

Yes. If an earlier edit is held on `candidate`, merging a later edit would ship the earlier
one too, so Ratchet holds the later one (`stacked_on_held`). Approve or roll back the
earlier edit first (ADR 0021).

## Why does the same task give different output on replay?

Token Factory doesn't honor `seed` (verified live), so even an unchanged skill produces
slightly different text. Replays run at temperature 0 with frozen time and cassettes, and the
judges compare meaning, not bytes. The benchmark's E6 run measures this noise.

## What happens if the agent tries to close an issue or send an email during a replay?

Nothing reaches the outside world. Side-effecting tools are mocked. A hard-class call with no
matching cassette gets `409 ratchet_blocked`, and Level 0 counts it as an unexpected side
effect, which BLOCKs the edit.

## The exchange rate changed and my report changed. Will Ratchet block it?

Not if the world really moved. For world-dependent tasks, Ratchet asks Tavily for the fact's
current value. If the fact changed and the new output follows from it, the pair is
**World changed** and the edit can pass. Press **Re-bless** to make today's result the new
reference. If Tavily can't confirm, the edit is held, never passed silently.

## Does Ratchet work with Hermes or OpenClaw?

Both use the same agentskills.io `SKILL.md` format, which Ratchet reads. The Hermes adapter
is built but not yet verified against a live install, so the default harness is the
MinimalRunner. A native OpenClaw adapter is on the roadmap.

## Where is my data kept?

In `RATCHET_DATA_DIR` (default `var/data`): an SQLite index, JSON traces, cassettes,
verdicts and the events table. Skills live in the git repo at `RATCHET_SKILLS_REPO`. PII in
traces is tokenized, and its token map is encrypted with a key outside the data directory.
Nothing is sent anywhere except model calls to Token Factory and searches to Tavily (in live
mode).

## What does the public demo record about me?

Anonymous page and button events: a random per-tab session hash, a device class, the
referrer host, and which scenario or button. There are no IP addresses, no user agents and no
third-party scripts, and rows are deleted after 30 days. See
[Demo analytics](security.md#demo-analytics).

## What is a "Recorded run"?

A verdict served from a run recorded earlier on live models, instead of computed now. Demo
hosts switch to it (`RATCHET_SIMULATE_MODE=replayed`) to spend nothing during an incident or
a busy period. It always carries the badge.

## How do I regenerate these docs?

`uv run python scripts/gen_docs.py && uv run --group docs mkdocs build --strict`. See
[Contributing to the docs](contributing.md).
