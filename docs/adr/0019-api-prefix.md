# 0019 — API prefix
Status: accepted
Date: 2026-10-15   Owner: Dashboard

## Context
Early links used /api/...; a versioned API needs /api/v1/....

## Decision
Routes live under /api/v1; /api/... is an alias for compatibility with those links.

## Consequences
The dashboard pins /api/v1.

## Reversal trigger
a v2 API
