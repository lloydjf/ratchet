# Contributing to Ratchet

Thanks for helping. Ratchet is regression CI for self-improving agents: it decides whether
an agent's self-edit is allowed to ship. Contributions are held to that standard. A change
that makes a verdict less trustworthy is a regression, however good the code is.

- **Questions and ideas:** [GitHub Discussions](https://github.com/lloydjf/ratchet/discussions).
- **Bugs and harness requests:** [issues](https://github.com/lloydjf/ratchet/issues/new/choose),
  using the templates.
- **Security problems:** never in public. See [SECURITY.md](SECURITY.md).
- **Conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) applies everywhere in the project.

## Development setup

You need Python 3.11, [uv](https://docs.astral.sh/uv/), Node.js 24 for the dashboard, and
Docker for the sandboxed and end-to-end paths. No keys are needed: everything runs against
the offline simulator by default.

```bash
git clone https://github.com/lloydjf/ratchet.git && cd ratchet
uv sync
cp .env.example .env                  # RATCHET_UPSTREAM=sim: offline, deterministic, free
(cd dashboard && npm ci)
uv run ratchet doctor
uv run ratchet seed-demo && uv run ratchet simulate 1 --headless   # expect BLOCK
```

Development uses the simulator. Never put a real key in a test, fixture, commit or issue,
and never present simulator output as model output: the simulator labels every verdict
**Simulated models**, and that label must survive your change.

## The gates

CI runs all of these on every pull request, and all must pass to merge. Run them locally
first.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy ratchet                    # strict
uv run pytest -q                       # unit + recorded integration; no network
uv run pytest tests/security -q        # secret-leak and sandbox tests
uv run pytest -q -m e2e                # the full local stack, on the simulator
```

Dashboard (from `dashboard/`):

```bash
npm run typecheck
npm run lint
npm test                               # vitest
npm run build
npm run e2e                            # playwright, fixture mode; npx playwright install chromium once
```

Also:

- Coverage stays at 80% or more on `ratchet/judge` and `ratchet/replay`.
- If you changed `ratchet/models.py` or the API, regenerate the schemas
  (`uv run ratchet schemas export` and
  `uv run ratchet api export-openapi > schemas/openapi.json`); CI fails on drift.
- If you changed the docs, the CLI, settings or the API, run
  `uv run python scripts/gen_docs.py` and
  `NO_MKDOCS_2_WARNING=1 uv run --group docs mkdocs build --strict`. See
  [Contributing to the docs](docs/user/contributing.md).
- Tests marked `live` call real models and cost money. They run only in a manual workflow.
  Do not run them in a pull request, and do not add network calls to the default suite.

## Engineering rules

The full contract is [`docs/CONTRACTS.md`](docs/CONTRACTS.md): module boundaries, the names
and wire shapes other code depends on, and the registry of stable error and reason codes. A
change to anything it lists is an interface change: update that file, its tests and every
caller in the same pull request. In short:

- Python 3.11, `ruff`, `ruff format`, `mypy --strict`. Modules under about 400 lines,
  functions under about 40, `__all__` on every module.
- I/O at the edges, logic in pure functions, no side effects at import time.
- Use `ratchet.clock.now()`, `ratchet.log.get_logger()`, `get_settings()`, `ratchet.util` and
  `ratchet.errors`, never `datetime.now()`, `os.environ` or bare exceptions. Every error
  carries a stable `code`.
- Tiers are the strings `nano`, `super` and `ultra`. Model ids live only in
  `ratchet/config/models.py`, prices only in `ratchet/config/pricing.py`.
- Data model changes in `ratchet/models.py` are additive. Don't rename fields.
- Every judge call goes through the structured-output helper. Decisions are table-driven,
  not scattered conditionals.
- Logs never contain content, arguments or secrets. PII is tokenized before anything is
  persisted.
- User-facing text lives in `ratchet/copy.py` and `dashboard/src/copy.ts`.
- Every core-loop action is available in both the dashboard and the CLI.

### The invariant: unknown never passes

Ratchet's one non-negotiable rule: **when Ratchet does not know, it does not pass.** An
unreadable judge answer, a timeout, a crashed replay, an unavailable Tavily, a failed sandbox
or any unexpected exception in a judge path becomes `uncertain` (held for a human) or stays a
`regression`. It never becomes `pass`. A replay must never be able to take a real
hard-class side effect.

Reviewers check this on every change to `ratchet/judge/`, `ratchet/replay/`,
`ratchet/mocktools/` and the orchestrator. If your change adds a new failure mode, add a test
that shows it lands on `uncertain`, not `pass`.

## Design changes and ADRs

Architectural decisions are recorded in [`docs/adr/`](docs/adr/) as numbered, short records:
context, decision, consequences. Write a new ADR (the next free number) when a change:

- alters what a verdict means, the agreement rules, or the decision table;
- changes the security boundary (sandbox, sidecar, secrets, public mode);
- adds a dependency, a service, a model tier role or a new runner;
- reverses or narrows an earlier ADR (mark the old one superseded; don't delete it).

Open the ADR as its own pull request, or as the first commit of the pull request that
implements it, and link it from the PR description. For anything large, start a Discussion
first so we can agree on the direction before you write the code.

## Adding a harness adapter

A harness adapter lets Ratchet record and replay another agent. Adapters live in
[`ratchet/replay/harness/`](ratchet/replay/harness/):

- [`base.py`](ratchet/replay/harness/base.py) defines the `HarnessAdapter` protocol
  (`configure`, `run_task`, `loaded_skills`, `reload_skills`, `pre_task_snapshot`) and
  `SandboxedHarness` (`plan` and `collect`, for running inside a sandbox).
- [`hermes.py`](ratchet/replay/harness/hermes.py) is the reference adapter. It shows the
  hard parts: isolated per-run state, model calls through the proxy's path form
  (`/v1/r/<run_id>`) with a dummy key, tools routed to the mock tool server over MCP, a
  one-shot invocation with a machine-readable event stream, and a stable prompt so the same
  skills and task produce the same request.
- [`minimal.py`](ratchet/replay/harness/minimal.py) is the smallest complete adapter.

An adapter must:

1. run one task, one shot, non-interactively, with a timeout;
2. send every model call through the capture proxy and every side-effecting tool through the
   mock tool server, with no other network access;
3. load skills only from the directory Ratchet gives it, and report which ones it loaded;
4. never see a real key, and never read or write the user's own agent state;
5. report failures as `harness_failed` or `harness_timeout`, so they hold the edit rather
   than pass it.

Register it in `get_adapter()`, add the name to `ratchet_harness` in
`ratchet/config/settings.py`, and add tests modeled on `tests/unit/test_harness_hermes.py`
and the end-to-end acceptance checks in `tests/e2e/test_hermes_acceptance.py`. Goldens and
replays must use the same adapter (ADR 0003). If you want an adapter but can't write it, open
a harness adapter request.

## Adding a benchmark skill

SkillRegress-Bench lives in [`bench/`](bench/). [`bench/README.md`](bench/README.md)
describes the layout, and [`bench/DATASHEET.md`](bench/DATASHEET.md) how skills, tasks,
edits and labels are built. A new skill needs its `SKILL.md` under `bench/skills/`, ten
tasks in `bench/tasks.yaml` (two of them edge cases), any world fixtures and workspaces it
uses, and a clean `uv run ratchet bench validate`. Check the pipeline with
`uv run ratchet bench smoke`, which runs on the simulator. Never quote smoke output as
results. Only maintainers run live sweeps, because they spend money against a fixed cap.

## Commits and pull requests

- **One logical change per pull request.** A refactor and a behavior change are two pull
  requests. A change that touches more than one subsystem asks for a reviewer for each.
- **Tests come with the change.** New behavior has tests; a bug fix has a test that failed
  before it. Test names state the behavior (`test_tavily_failure_keeps_pair_regression`).
- **Commit messages:** an imperative subject of 72 characters or fewer, prefixed with the
  area (`judge: cite steps in the Super check`, `dashboard: show control outcome on pairs`),
  and a body that says why.
- **Fill in the [pull request template](.github/pull_request_template.md).** The review
  checklist in it is what reviewers tick.
- **Update the docs and the changelog.** User-visible changes get a line under
  `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), and the matching page in `docs/user/`.
- **No secrets, no real personal data.** Fixtures use the fictional world in `scenarios/`
  and `bench/world/`.

By contributing, you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), and benchmark data under CC BY 4.0.
