# 0022 — Hardening after the 2026-10-16 security and spec review
Status: accepted
Date: 2026-10-16   Owner: Core

## Context
The review found one critical issue (C1: `shell.run` options such as
`sort --compress-program=sh` ran code), two high ones (H1: a stacked merge could ship a
reverted regression; H2: sidecar spending caps were checked against an empty scratch
store), and a set of medium/low ones around trust in sandbox-supplied headers, cassette
matching, prompt injection into judges, unbounded queues and streams, and secrets.

## Decision
- **Read-only shell in Python.** `shell.run` no longer starts a process. `ls`, `cat`,
  `head`, `tail`, `wc`, `grep` are reimplemented with a per-command allowlist of single
  short flags (only `head`/`tail -n <digits>` take a value); every other argument is a path
  that must resolve inside the workspace. `sort`, `uniq`, `diff`, `find`, `cut` are gone.
- **A revert only resolves an earlier edit once it lands with the merge.** The stacking
  guard treats a reverted ancestor as resolved only when its revert commit is an ancestor
  of the merge target, and "decided but not yet acted" as unresolved. Rolling back a
  merged edit that main cannot take is refused (`stacked_on_held`), never recorded as done.
- **Approve on a reverted verdict means "approve anyway".** Approve on any verdict whose
  action is `reverted` (a BLOCK or a user rollback) reverts the revert on candidate and
  merges under the stacking guard (`action="merged"`, `approved_at` set, `rolled_back_*`
  kept). Rollback on a reverted verdict is 409 `already_reverted`; Approve on a merged one
  is idempotent; both answer 409 `not_ready` until the edit's pipeline is terminal, and
  the automatic acting stage takes the same per-verdict lock and re-reads the verdict.
- **The sandbox is never trusted for policy.** The worker's sidecar proxy forces
  `purpose=agent` and the run's tier, enforces the dispatch-time allowance
  (`ReplayBatch.budget_allowance_usd`, computed from the control-plane ledger) against its
  scratch spend, and keeps its admin routes behind a per-golden token the sandbox never sees.
- **Hard side effects match cassettes exactly.** Tolerant matching is for reads; a
  reversible write must also match on recipient/channel/amount/id-like keys.
- **Judge inputs are data.** Every untrusted block in a judge prompt is wrapped in
  nonce-tagged delimiters, and only a reply that is a single JSON object (else the last
  top-level object) is parsed.
- **Bounded public surface.** Simulate has a queue cap and one active run per client IP;
  SSE has per-IP and global stream caps and one shared poller; request bodies are capped
  at 64 KiB in Caddy and the app; public mode refuses the default action-token secret.

## Consequences
A cosmetic rewording of a hard side-effect call (for example a `github.comment` body) now
counts as an unexpected side effect, so Level 0 blocks it; this is the safe direction.
Agents that relied on `sort`/`find` in `shell.run` get a refusal message listing the six
commands. Known limitations are listed in `docs/RUNBOOK.md` ("Security limitations").

## Reversal trigger
A sandbox boundary that is verified live and strong enough that defence in
depth in the tool layer costs more than it protects.
