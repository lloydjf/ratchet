# 0005 — Cassettes for side-effecting tools
Status: accepted
Date: 2026-10-16   Owner: Replay

## Context
Replays must never cause real side effects.

## Decision
Side-effecting tools are served from cassettes; pure local tools execute for real inside the sandbox.

## Consequences
Cassette misses expose unexpected actions (Level 0 evidence).

## Reversal trigger
—
