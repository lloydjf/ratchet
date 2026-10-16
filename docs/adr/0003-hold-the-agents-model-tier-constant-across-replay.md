# 0003 — Hold the agent's model tier constant across replay
Status: accepted
Date: 2026-10-16   Owner: Judge

## Context
A divergence is only meaningful if it is attributable to the skill edit.

## Decision
Replays run on the golden's agent tier; the proxy refuses a mismatch with 409 tier_mismatch.

## Consequences
Tiering applies to judging, screening, extraction and narration only.

## Reversal trigger
never; this is the product's honesty
