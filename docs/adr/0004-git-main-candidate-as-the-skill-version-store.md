# 0004 — Git main/candidate as the skill version store
Status: accepted
Date: 2026-10-16   Owner: Skills

## Context
Edits must be reversible and reviewable without custom versioning.

## Decision
The skills directory is a git repo; Ratchet writes candidate, the harness reads main.

## Consequences
Normal review tools work; rollback is git revert; history is never rewritten.

## Reversal trigger
a harness that cannot read skills from a checkout
