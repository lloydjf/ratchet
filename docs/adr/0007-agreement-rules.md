# 0007 — Agreement rules
Status: accepted
Date: 2026-10-16   Owner: Judge

## Context
A single LLM judge produces false blocks.

## Decision
BLOCK needs a Level 0 hard fail or Super+Ultra agreement; single-judge regressions and uncertain pairs WARN.

## Consequences
Bounds false blocks; makes the cascade visible.

## Reversal trigger
bench false-block > 5% with a better rule found
