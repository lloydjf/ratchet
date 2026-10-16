# 0017 — Process sandbox for development
Status: accepted
Date: 2026-10-15   Owner: Core

## Context
Docker is not always available on a developer machine and OpenShell needs Linux; tests and local dev still need to replay.

## Decision
Add RATCHET_SANDBOX=process: the harness runs as a subprocess with a scrubbed environment and a dummy key. It is refused when RATCHET_PUBLIC_MODE=true.

## Consequences
Egress is not enforced in process mode; only the MinimalRunner, which talks solely to the proxy and mock tools, is supported there. The public host uses docker or openshell.

## Reversal trigger
a CI environment without Docker becomes the deployment target
