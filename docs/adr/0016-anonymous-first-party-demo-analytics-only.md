# 0016 — Anonymous first-party demo analytics only
Status: accepted
Date: 2026-10-16   Owner: Demo

## Context
A judge-facing page must not load vendor scripts.

## Decision
Anonymous demo_events table, session hash in sessionStorage, no cookies beyond the session key, no IPs.

## Consequences
Privacy; funnel reliability visible.

## Reversal trigger
—
