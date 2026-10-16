# ADR 0024 — Level 0 gates the Nano-only pass

**Status:** accepted, 2026-10-16

## Context

The first live SkillRegress-Bench sweep (`bench/results/2026-10-16`) put edit-level detection
at 0.64 with the full cascade against 1.00 for Ultra alone. The loss was in the Nano screen:
a pair Nano calls `equivalent` with confidence ≥ 0.85 never reaches Super or Ultra. Every
one of the ten R3 (tool-argument change) pairs Nano cleared alone carried a Level 0
"changed args" finding, and none of the 92 cosmetic-edit (N0) pairs Nano cleared had any
Level 0 finding. Final outputs told the same story: 90 % of true-pass pairs had a
golden-vs-replay similarity of 1.0, while the regressions Nano cleared had a median of 0.92
and a first decile of 0.59.

Before this change Level 0 only forbade the shortcut for a missing hard-class call or a
re-argued hard-class call.

## Decision

`Level0Finding.output_similarity` is the `difflib` ratio of the two final outputs with
whitespace collapsed and case folded (1.0 when both are empty). The Nano screen may not clear
a pair alone when Level 0 found **any** structural difference (a missing, new or re-argued
side-effect call of any class) or when `output_similarity < 0.90`
(`cascade.OUTPUT_SIMILARITY_FLOOR`). Such a pair goes to Super as before; Nano's opinion is
still recorded and shown.

## Consequences

- Cost: only pairs with a structural finding or a materially different output pay for Super;
  on the sweep that is about one pass pair in ten plus the pairs that needed it.
- A rejected alternative, measured on the same sweep: tolerating `uncertain` pairs up to the
  E6 noise floor at decision time would have turned two cosmetic WARNs into PASS but also
  five real regressions (R1, R2, R5 ×2, R6). Unknown still never passes.
- Nano's own misses on near-identical outputs are not addressed here; the bench's pair-level
  labels are themselves noisy (about half of the "missed" pairs had identical outputs, i.e.
  the regression did not manifest on that replay).
