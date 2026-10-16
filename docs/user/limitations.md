# Limitations

Ratchet v0.1 was built in fifteen days. This page lists what it does not do yet, and where
its evidence stops.

## Evidence and verification

- **One harness so far.** Every published number was produced with the MinimalRunner. The
  Hermes adapter passes its acceptance checks against a real Hermes Agent v0.22.2 install on
  the offline simulator, but no Hermes run has used a live model yet.
  `RATCHET_HARNESS=minimal` is the default.
- **Benchmark results are a pilot.** The live pilot sweep of SkillRegress-Bench (2026-10-16)
  is in `bench/results/2026-10-16/report.md`. `bench/results/smoke-sim/` is a simulator smoke
  test, not results.
- **The benchmark is synthetic.** Its tasks and world are invented, and its regressions are
  injected by an LLM, not observed in the wild. The live sweep is a pilot, sized to the $12
  cap. See [Benchmark: limitations](benchmark.md#limitations).
- **OpenShell and Serverless Jobs are not verified live.** The OpenShell sandbox
  and some Nebius Jobs API fields and the cold start still need a Nebius VM and Jobs
  quota. The verified path is the Docker sandbox and the local runner.

## Judging

- **Nondeterminism is a floor.** Token Factory does not honor `seed` (verified live), so
  replays of an unchanged skill still vary. Ratchet judges meaning rather than bytes, and the
  benchmark's E6 run measures the noise floor. Read every recall figure against it.
- **The drift check is only as good as public evidence.** If a fact isn't findable on the
  web, the pair stays "not sure" and the edit is held for a human. It is never passed
  silently.
- **Judges are models.** The agreement rules (two judges to block) and Level 0 bound false
  blocks, but a WARN still needs a person to read it.
- **The cascade costs more on contested edits.** Every regression-leaning pair gets an Ultra
  confirmation (ADR 0020). On an edit where most pairs are contested, like the seeded
  scenarios, the cascade saves about 1.7–2.3× against all-Ultra, not more.

## Scope

- **Not a security scanner.** Ratchet catches regressions against your own history. It does
  not look for malicious skills (NVIDIA SkillSpector covers that).
- **Not multi-tenant.** There are no accounts, roles or per-user isolation. A local install
  trusts anyone who can reach its port.
- **Rollback is a git revert, not a guarantee.** It restores the skill files. It does not undo
  what an agent already did with an edit that reached `main`.
- **Held edits resolve in order.** An earlier held edit blocks later merges until you approve
  or roll it back (ADR 0021). Cherry-picking a later edit past an earlier one is not supported.
- **Recording uses Ratchet's own harness.** In v0.1, blessable runs come from
  `ratchet agent run` (or the demo seeding), with tools from Ratchet's catalog served by the
  mock tool server. Tool calls that bypass the mock tool server and the workspace aren't
  captured.
- **One world per install.** `ratchet agent run` records against the world fixtures in
  `RATCHET_SCENARIOS_DIR/world`.

## What would come next

From the roadmap: a one-line Hermes install (v0.2); a native
OpenClaw adapter and a GitHub Action for skills repos (v0.3); teams and shared golden
libraries (v0.4).

## A regression on a noisy task may be invisible

If a task already varies between two runs of the unchanged skill, one replay cannot show
what an edit did to it: the control replay diverges and the pair counts as a noisy pass.
The other pairs carry the signal, and the benchmark reports the share of noisy pairs, but
an edit that only breaks tasks that were already noisy can slip through.

