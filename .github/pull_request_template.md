<!-- Title: what the change does, e.g. `judge: Super check with evidence steps`. One concern per PR. -->

**Issue:** <!-- Link the issue this resolves, or explain why none is needed. -->

## What

<!-- The change, in two or three sentences. -->

## Why

<!-- Link the design doc or ADR this implements, e.g. `docs/design/orchestrator-api-cli.md` or `docs/adr/0020-ultra-confirms-cascade-blocks.md`. -->

## How tested

<!-- The tests you added or changed (unit, integration, e2e, security) and the commands you ran. -->

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy ratchet`
- [ ] `uv run pytest -q` (coverage stays ≥ 80% on `ratchet/judge` and `ratchet/replay`)
- [ ] `uv run pytest tests/security -q`
- [ ] `uv run pytest -q -m e2e` (if the loop, replay or judging changed)
- [ ] Dashboard: `npm run typecheck && npm run lint && npm test && npm run build && npm run e2e` (if UI changed)
- [ ] Docs: `NO_MKDOCS_2_WARNING=1 uv run --group docs mkdocs build --strict` (if docs, CLI, settings or API changed)
- [ ] `CHANGELOG.md` has a line under `## [Unreleased]` (if users will notice)

## VERIFY items touched

<!-- e.g. a VERIFY marker on the Jobs field names. Each one updated in docs/VERIFIED.md, or "none". -->

## Screenshots

<!-- UI changes: before/after, dark and light, desktop and phone. Otherwise delete. -->

## Review checklist

The reviewer ticks every group; an item that does not apply is ticked with "n/a".

**Correctness**

- [ ] The change matches the design doc or ADR it cites; if the doc is wrong, this PR edits the doc too
- [ ] Tiers are named `nano`/`super`/`ultra` everywhere; model ids only in `ratchet/config/models.py`
- [ ] Every judge call goes through the structured-output helper with validation and retries
- [ ] Unknown never passes: every new failure in a judge or replay path lands on `uncertain` (held) or stays `regression`, never `pass`, with a test showing it
- [ ] No code path lets a replay take a hard-class side effect for real (must be impossible)
- [ ] The replayed agent's model tier is identical to the golden's
- [ ] Decisions are table-driven (the agreement rules in ADR 0007 and ADR 0020), not scattered conditionals

**Determinism and evidence**

- [ ] Temperature 0, seed passthrough, frozen time, cassettes in replay paths
- [ ] Every opinion cites step ids that exist
- [ ] Every verdict stores enough to re-render the page without re-calling a model

**Safety and secrets**

- [ ] No secret in a trace, cassette, log, sandbox env or git; the secret-leak tests in `tests/security` pass
- [ ] PII tokenized before persistence
- [ ] Sandbox egress limited to the proxy and mock tools

**Cost**

- [ ] Purpose tag on every proxy call; caps respected; no unbounded loops of model calls
- [ ] Nano before Super before Ultra; Ultra never called on a pair Level 0 already decided

**Product**

- [ ] A user could understand the output without reading code: labels, tiers, dollars, one-sentence reasons
- [ ] No CLI-only path for the core loop; the dashboard can do it

**Hygiene and config**

- [ ] Tests for the new behavior exist and pass; coverage not reduced
- [ ] No TODO without a linked issue; no commented-out code
- [ ] No model strings, URLs or prices outside `ratchet/config/`
- [ ] New config fields in `ratchet/config/settings.py`, `.env.example` (`uv run python scripts/gen_env_example.py`) and `docs/user/configuration.md` (`uv run python scripts/gen_docs.py`)
- [ ] New log events documented; no content, arguments or secrets logged
- [ ] `docs/VERIFIED.md` updated if a VERIFY was touched
- [ ] New dependency? one-line justification here
- [ ] Touches more than one subsystem? a reviewer for each requested
