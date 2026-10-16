# 0010 — OpenShell primary, Docker fallback
Status: accepted
Date: 2026-10-16   Owner: Infra

## Context
OpenShell needs Linux >= 6.2 with Landlock; running inside a Serverless Job container is undocumented.

## Decision
Docker sandbox in jobs; OpenShell adapter kept and used on the VM; the live sandbox check on a Nebius Linux VM decides whether OpenShell becomes primary.

## Consequences
The product does not depend on an unverified runtime.

## Reversal trigger
The live OpenShell check on a Linux VM passes, or shows OpenShell cannot run there.
