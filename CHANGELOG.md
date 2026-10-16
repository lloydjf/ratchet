# Changelog

All notable changes to Ratchet are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0, a minor version may
change interfaces listed in [`docs/CONTRACTS.md`](docs/CONTRACTS.md); every such change is
called out here.

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-10-16

The first public release: one person's agent, one skills repo, checked on every self-edit.

### Added

- **Capture proxy.** An OpenAI-compatible proxy in front of Token Factory records every model
  call with its tier, purpose, tokens and cost, with streaming, a response cache for judge
  calls, and daily, per-purpose and benchmark spending caps. Point your harness's base URL
  at `http://127.0.0.1:8081/v1`.
- **PII tokenization.** Emails, phone numbers and secrets are replaced with tokens
  (`<EMAIL_1>`, `<PHONE_1>`, `<SECRET>`) before a trace or cassette is written. The token
  map is encrypted, with its key kept outside the data directory.
- **Mock tool server and cassettes.** Side-effecting tools are recorded once and served from
  cassettes on replay. A hard-class call with no matching cassette is refused with
  `409 ratchet_blocked`, so a replay can never take a real side effect.
- **Bless and protect.** `ratchet bless` (or **Bless** in the dashboard) turns a run you are
  happy with into a golden trace. At bless time Nano extracts the world facts the run relied
  on and the success criteria it must keep meeting. Goldens can be unblessed, their criteria
  edited, and drifted ones re-blessed.
- **Skill edit detection.** The skills directory is a git repo. Writes to `main` are moved
  onto a `candidate` commit, so `main` only changes through Ratchet. OpenClaw-style skills
  directories work read-only.
- **Replay.** Each edit replays the goldens that use the edited skill, the goldens its new
  trigger text could fire on, and 3 canaries, in fresh sandboxes, on the same model tier as
  the original run.
- **Level 0.** A deterministic side-effect diff, with no model, decides a pair outright when
  a replay makes a hard-class call the golden never made.
- **Nemotron cascade.** Nano screens every pair, Super checks with cited steps, and Ultra
  adjudicates contested pairs. A BLOCK needs a Level 0 hard fail or Super and Ultra
  agreeing. Every judge call is schema-validated with retries, and an unreadable answer
  becomes "not sure", never a pass.
- **Tavily drift checks.** For world-dependent tasks, a divergence only counts as a
  regression after Tavily shows the world did not move. A confirmed change is judged
  `drift`, and the edit passes with **Re-bless** offered. If Tavily is unavailable, the pair
  stays a regression.
- **Control replays.** Contested pairs are replayed once more with the unchanged skill. If
  the unchanged skill also diverges, the difference is treated as nondeterminism and the
  pair becomes "not sure" (held, not blocked). A control never turns a pair into a pass.
- **Decisions and git actions.** PASS merges to `main`, WARN holds the edit on `candidate`,
  BLOCK reverts it. Every action is a git commit with an undo path: `ratchet rollback`,
  `ratchet approve` (including approve-anyway on a BLOCK) and `ratchet rebless`. Held edits
  resolve in order, so a later merge never ships earlier unresolved work.
- **Plain-English verdicts.** Ultra writes a short summary for each verdict, with a
  deterministic fallback when the summary fails validation.
- **Dashboard.** A timeline of edits, verdict pages with the skill diff and per-pair
  evidence, live progress for every stage, cost by tier against an all-Ultra
  counterfactual, the golden library, product metrics and a weekly digest. Dark and light
  themes. Every action in the core loop is available in the dashboard and the CLI.
- **Simulate.** Three seeded scenarios (a Level 0 BLOCK, a cascade BLOCK and a PASS with
  drift) that run the whole loop on a private copy of the demo agent's skills.
- **Harnesses.** The MinimalRunner (the default, and the harness behind every published
  number) and a Hermes Agent adapter that runs `hermes chat --oneshot` with isolated state,
  tools over MCP and model calls through the proxy. The Hermes adapter passes its
  acceptance checks against Hermes v0.22.2 on the offline simulator and is off by default.
- **Runners and sandboxes.** A local runner (process for development, or Docker), a Docker
  sandbox with no network beyond the proxy and the mock tools, an OpenShell sandbox with a
  deny-all policy, and a Nebius Serverless Jobs runner that ships batches of up to 10
  replays through Object Storage.
- **Offline simulator.** `RATCHET_UPSTREAM=sim` runs everything without keys, for CI and a
  first local run. Every verdict it produces carries a **Simulated models** badge.
- **SkillRegress-Bench.** 10 skills, 103 tasks, a regression taxonomy R1–R6, N0 cosmetic and
  D1 world-drift controls, and `ratchet bench` commands to build, label, run, report and
  compare, with a cost estimate and a budget guard before any spend. The live pilot sweep of
  2026-10-16 is published in
  [`bench/results/2026-10-16/report.md`](bench/results/2026-10-16/report.md): on 35
  labeled edits the cascade had detection recall 0.80 with zero false blocks on cosmetic and
  world-drift controls, drift accuracy 1.00, and $0.0384 mean cost per verdict. It also held
  7 of 8 cosmetic edits for review; see the report's limitations.
- **Docs site.** Getting started, concepts, dashboard and benchmark guides, CLI,
  configuration and API references generated from the code, operations, security and
  privacy, and limitations.
- **Deployment.** `docker compose up control`, and scripts for a single VM with Caddy TLS,
  systemd units, nightly backups, token rotation and alerts.

### Known limitations

- Every published number comes from the MinimalRunner. No Hermes run has used a live model
  yet.
- OpenShell and some Serverless Jobs API fields are not verified live; Docker and the local
  runner are the verified path.
- Token Factory does not honor `seed`, so replays of an unchanged skill still vary.
- Single user, no dashboard authentication: keep a local install bound to `127.0.0.1`.

See [Limitations](docs/user/limitations.md) for the full list.

[Unreleased]: https://github.com/lloydjf/ratchet/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lloydjf/ratchet/releases/tag/v0.1.0
