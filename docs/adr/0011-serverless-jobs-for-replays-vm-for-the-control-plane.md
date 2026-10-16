# 0011 — Serverless Jobs for replays; VM for the control plane
Status: accepted
Date: 2026-10-16   Owner: Infra

## Context
Serverless Endpoints have no autoscaling or scale-to-zero; job cold starts are minutes.

## Decision
Jobs run replay batches; the control plane is a VM; the judge path may use the local runner.

## Consequences
Serverless story for judges without betting the demo on cold starts.

## Reversal trigger
Measured Serverless Job cold starts short enough for the judge path.
