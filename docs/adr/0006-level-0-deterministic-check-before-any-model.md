# 0006 — Level 0 deterministic check before any model
Status: accepted
Date: 2026-10-16   Owner: Judge

## Context
The cheapest, most defensible BLOCK needs no model.

## Decision
A new hard-class side effect in a replay is a regression decided without a model.

## Consequences
Ultra is never called on a pair Level 0 decided.

## Reversal trigger
—
