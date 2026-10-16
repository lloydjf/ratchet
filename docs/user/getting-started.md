---
hide:
  - navigation
---

# Get started

This page takes you from a clone to a verdict on your own machine. With no API keys, it
takes about five minutes, and everything runs against the offline simulator. After that,
you switch to real models and bless a task of your own.

## Requirements

| Tool | Version | Used for |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | recent | Python 3.11 and the locked dependencies |
| Python | 3.11 (uv installs it) | Ratchet itself (`requires-python = ">=3.11,<3.12"`) |
| Node.js and npm | 24 (the version the control image builds with) | building the dashboard once |
| git | any recent | the skills repo (`main` / `candidate`) |
| Docker | optional | the Docker sandbox, the Docker runner, `docker compose` |

## 1. Install

```bash
git clone https://github.com/lloydjf/ratchet && cd ratchet
uv sync                                   # Python 3.11 venv + locked dependencies
cp .env.example .env                      # RATCHET_UPSTREAM=sim: fully offline
cd dashboard && npm ci && npm run build && cd ..
```

The dashboard build writes `dashboard/dist/`, which `ratchet serve` serves on port 8080.

## 2. Choose simulated or live models

`.env.example` starts in **simulated** mode. Nothing calls a paid API, and every verdict is
labeled <span class="badge warn">Simulated models</span>.

=== "Simulated (default, no keys)"

    ```bash
    RATCHET_UPSTREAM=sim
    RATCHET_TAVILY_MODE=fixtures
    ```

    The offline simulator (ADR 0018) answers agent calls from scripted skill policies and
    judge calls with heuristics over the prompt sections. It is deterministic and free. Use
    it for CI, development and a first look. It is not Nemotron, and Ratchet never
    presents its output as Nemotron output.

=== "Live (real Nemotron and Tavily)"

    ```bash
    NEBIUS_TOKEN_FACTORY_KEY=<your Token Factory key>
    RATCHET_UPSTREAM=live
    TAVILY_API_KEY=<your Tavily key>
    RATCHET_TAVILY_MODE=live
    RATCHET_DAILY_USD_CAP=10
    ```

    Only the capture proxy reads the Token Factory key; it never enters a replay sandbox.
    The daily cap is enforced on every call (see [Budget caps](configuration.md#budget-caps)).
    Tavily is optional: without it, drift checks use recorded fixtures.

Then check the setup:

```bash
uv run ratchet doctor
```

`doctor` prints one row per check: settings, models, runner, sandbox, Tavily, git, disk,
caps and scenarios. A first run on a laptop looks like this:

```text
check      status  detail
---------  ------  ---------------------------------------------------------
settings   ok      upstream sim, data var/data
models     ok      offline simulator (verdicts are labeled Simulated models)
runner     ok      local (process)
sandbox    warn    process sandbox: dev only, egress not enforced
tavily     ok      recorded fixtures
git        warn    no skills repo yet; run `ratchet seed-demo`
disk       ok      …
caps       ok      $0.0000 of $10.00 today
scenarios  ok      3 scenarios, mode live
```

`warn` is fine for a first run. `fail` exits non-zero and says what to fix. With
`RATCHET_UPSTREAM=live`, `models` also checks that the nano, super and ultra ids are in the
Token Factory catalog (`GET /models`).

## 3. Seed the demo agent

```bash
uv run ratchet seed-demo
```

This initializes the skills git repo at `var/skills` (with its `candidate` worktree at
`var/skills-candidate`) from `scenarios/skills/`. Then it records and blesses the 25 demo
tasks in `scenarios/golden/tasks.yaml`: 11 github-triage, 7 expense-report,
4 fx-expense-converter and 3 canaries. Everything in `scenarios/` is synthetic: the fictional
org `acme`, people at `example.com`.

```text
Demo seeded: 25 recorded, 0 already blessed, 3 canaries, $0.0687.
```

On the simulator the dollar figure is priced from simulated token counts, and nothing is
spent. On live models, `seed-demo` prints its estimate first (about $0.10). It is
idempotent: run it again and blessed tasks are skipped. `--force` re-records every golden.

## 4. Run a scenario from the terminal

```bash
uv run ratchet simulate 1 --headless      # github-triage closes stale issues → BLOCK
uv run ratchet simulate 2 --headless      # expense-report drops the VAT line → BLOCK
uv run ratchet simulate 3 --headless      # the EUR/USD rate moved → PASS with drift
```

```text
Simulate 1: Close stale issues after triage (verdict_69655afccb47)
    0.5s  Detected edit
    0.9s  Replaying on the local runner (0/14)
    5.0s  Replaying on the local runner (14/14)
    5.6s  Done
BLOCK (reverted) in 5.8s [Simulated models]
Blocked: the new github-triage would close issues in 6 of 14 replays.
Expected BLOCK: matches.
http://127.0.0.1:8080/verdicts/verdict_69655afccb47
```

!!! tip "Quieter output"
    Ratchet writes structured JSON log lines to stdout, interleaved with the command's own
    output. Set `RATCHET_LOG_LEVEL=WARNING` to keep only warnings and errors in the terminal.

`simulate` exits 0 only when the decision matches the scenario's expected one, so it doubles
as a smoke test. Each run applies the scenario's edit to a per-session copy of the skills
repo, so your `var/skills` repo is never touched.

## 5. Open the dashboard

```bash
uv run ratchet serve
```

One process starts the dashboard and API on <http://127.0.0.1:8080>, the capture proxy on
8081, the mock tool server on 8082, and the orchestrator with the skills-repo watcher. Open
the dashboard, press **Simulate** on a card, and watch the progress strip. The verdicts from
step 4 are already on the timeline. See [Dashboard](dashboard.md) for every screen.

`--no-watch` starts everything except the watcher.

## 6. Bless a task of your own

A golden trace is a finished run that you've marked as correct. In v0.1, Ratchet records
runs with its own harness (the MinimalRunner by default) on the control plane. The run's
model calls go through the capture proxy, and its tool calls go to the mock tool server in
record mode, against the world in `scenarios/world/`.

```bash
uv run ratchet agent run "File taxi-2026-10-01.pdf as an expense"
# prints: run_… (ok) and the agent's final output
uv run ratchet bless --last               # or: uv run ratchet bless <run_id>
```

`agent run` prints the run id and the final output. Read the output before you bless it:
blessing tells Ratchet "this is what correct looks like".

Blessing does four things: a PII pass that tokenizes emails, phone numbers and secrets; Nano
extracts the world facts the output relied on; side-effect classes are assigned; and Nano
infers the success criteria the task must keep meeting. You can bless from the dashboard too:
open **Traces → Show recent runs**, open the run, and press **Bless**.

!!! tip "Write good tasks to bless"
    Bless tasks whose outputs you would notice if they broke: a report that must include a
    VAT line, a triage that must label without closing. Each skill's `SKILL.md` should spell
    out its output format. The seeded skills do (`VAT: <amount> <currency>` on its own line),
    so the judges can tell a dropped field from a reworded one.

## 7. Edit a skill and get a verdict

With `ratchet serve` running, change a skill in the `main` checkout, the way an agent would:

```bash
$EDITOR var/skills/expense-report/SKILL.md
```

The watcher debounces for 750 ms. Then it moves the change onto a `candidate` commit and
restores `main`, so your agent keeps reading the old skill. Ratchet selects the affected
goldens, replays them, judges them and decides. The verdict appears on the timeline, and
under **Verdicts**:

- <span class="chip pass">✓ PASS</span>: merged into `main`. Your agent uses the new skill on its next task.
- <span class="chip warn">! WARN</span>: held on `candidate`. **Approve** merges it; **Rollback** reverts it.
- <span class="chip block">✕ BLOCK</span>: reverted on `candidate`; `main` never changed. **Approve anyway** re-applies it.

Writes in the `main` checkout are attributed to the agent; writes in the `candidate`
worktree (`var/skills-candidate`) are attributed to you.

## Docker compose

The same stack runs in one container, with no keys:

```bash
docker compose up --build control         # http://127.0.0.1:8080, simulator, no keys
docker compose run --rm control doctor    # any ratchet command
docker compose down                       # keeps the ratchet-srv volume
docker compose down -v                    # also deletes data and skills
```

The root `compose.yaml` includes `deploy/docker-compose.yml`, so both spellings work. The
`control` container reads `.env` when it exists. Its entrypoint migrates the database,
seeds the demo (idempotent) and runs `ratchet serve`. The dashboard is published on
`127.0.0.1` only. The proxy (8081) and mock tools (8082) stay inside the container. To run
replays as sibling containers with the Docker sandbox, see the header of
`deploy/docker-compose.docker-runner.yml`. It mounts the Docker socket, which makes the
container root-equivalent on the host, so use it for local integration only.

## Troubleshooting

Start with `uv run ratchet doctor`. Each row names what it checked.

| Symptom | Cause and fix |
| --- | --- |
| `models fail: NEBIUS_TOKEN_FACTORY_KEY is not set` | Live mode without a key. Set the key, or use `RATCHET_UPSTREAM=sim` |
| `models fail: model ids not in the catalog for: …` | A `RATCHET_MODEL_*` override or a catalog change. Remove the override, or check [Verified](reference/verified.md) |
| `sandbox warn: process sandbox: dev only` | Expected on a laptop. For real isolation, use `RATCHET_SANDBOX=docker` ([matrix](configuration.md#runners-sandboxes-and-harnesses)) |
| `sandbox fail: process sandbox is refused in public mode` | Public mode needs `docker` or `openshell` |
| `sandbox fail: docker not found on PATH` | Install Docker, or switch the sandbox |
| `git warn: no skills repo yet` | Run `uv run ratchet seed-demo` |
| `git fail: candidate worktree missing` | The `<skills>-candidate` worktree was deleted. Re-run `seed-demo` on a fresh `RATCHET_SKILLS_REPO`, or restore the worktree with git |
| `tavily fail: live mode but TAVILY_API_KEY is not set` | Set the key, or use `RATCHET_TAVILY_MODE=fixtures` |
| `caps warn` | Today's spend is at 80 % or more of `RATCHET_DAILY_USD_CAP` |
| `scenarios fail: no scenarios under …` | `RATCHET_SCENARIOS_DIR` points at the wrong place |
| `ratchet serve` exits with `config_error` about the action token | Public mode with the default or a short `RATCHET_ACTION_TOKEN_SECRET`. Set a long random secret |
| Dashboard at :8080 is blank or 404 | Build it: `cd dashboard && npm ci && npm run build` |
| A verdict is held with "Spending cap reached for today" | The daily or a purpose cap was hit. Raise it in `.env` and restart, or wait for 00:00 UTC |
| Simulate answers "Simulate is busy" | The queue is full, or you already have a run going. Wait for it to finish |
| `error not_seeded` | Run `uv run ratchet seed-demo` first |

For one edit's full story, `uv run ratchet logs --edit <edit_id>` prints its events and
worker logs, and `uv run ratchet status --timing <verdict_id>` prints the stage timings.
