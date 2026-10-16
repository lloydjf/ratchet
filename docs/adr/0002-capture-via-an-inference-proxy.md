# 0002 — Capture via an inference proxy
Status: accepted
Date: 2026-10-16   Owner: Capture

## Context
Ratchet must capture model and tool calls from more than one harness without forking them.

## Decision
Capture via an OpenAI-compatible inference proxy, not harness hooks.

## Consequences
Harness-agnostic; cost and tier data for free. Tool calls that bypass the model are captured by the mock tool server and the workspace diff.

## Reversal trigger
Hermes exposes a stable trace hook that is strictly richer
