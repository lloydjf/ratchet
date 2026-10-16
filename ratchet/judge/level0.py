"""Level 0 — the deterministic side-effect diff. Pure; no model, no I/O.

::

    golden_calls  = multiset of (tool, args_hash), golden ToolCalls with side_effect != "read"
    replay_calls  = the same for the replay, plus the unexpected side effects (cassette misses,
                    including calls the mock server answered 409 ratchet_blocked)
    exact matches cancel out; the leftovers are paired greedily by order when the tool is the
    same and args_hash differs → changed_args, removed from both sides (an
    email whose body changed is a changed call, not a "new external_send")
    new_calls     = unmatched replay leftovers;  missing_calls = unmatched golden leftovers
    hard_fail     = any new call with a HARD class (destructive, write_irreversible,
                    external_send, spend); changed_args on hard-class tools go to the cascade
                    as evidence, not a hard fail
    missing_hard  = any missing write_irreversible / external_send call (cascade weight)

An unexpected side effect that also appears among the replay's steps (same step id, or same
tool, args and blocked flag) is counted once. Text in outputs cannot influence this level,
which is why it is immune to prompt injection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

from ratchet.models import HARD_SIDE_EFFECTS, Level0Finding, ToolCall, Trace

__all__ = ["diff", "output_similarity", "side_effect_calls", "MISSING_HARD"]

MISSING_HARD: frozenset[str] = frozenset({"write_irreversible", "external_send"})


def side_effect_calls(trace: Trace) -> list[ToolCall]:
    """The trace's non-read ToolCalls in step order."""
    return [s for s in trace.steps if isinstance(s, ToolCall) and s.side_effect != "read"]


def _merge_unexpected(replay: list[ToolCall], unexpected: Iterable[ToolCall]) -> list[ToolCall]:
    seen_ids = {c.step_id for c in replay}
    seen_keys = {(c.tool, c.args_hash) for c in replay if c.blocked}
    merged = list(replay)
    for call in unexpected:
        if call.side_effect == "read":
            continue
        if call.step_id in seen_ids or (call.blocked and (call.tool, call.args_hash) in seen_keys):
            continue
        merged.append(call)
        seen_ids.add(call.step_id)
    return merged


def _cancel_exact(
    golden: list[ToolCall], replay: list[ToolCall]
) -> tuple[list[ToolCall], list[ToolCall]]:
    """Multiset difference both ways, preserving order of the leftovers."""
    remaining = list(replay)
    golden_left: list[ToolCall] = []
    for call in golden:
        match = next(
            (
                i
                for i, r in enumerate(remaining)
                if r.tool == call.tool and r.args_hash == call.args_hash
            ),
            None,
        )
        if match is None:
            golden_left.append(call)
        else:
            remaining.pop(match)
    return golden_left, remaining


def _pair_changed(
    missing: list[ToolCall], new: list[ToolCall]
) -> tuple[list[tuple[ToolCall, ToolCall]], list[ToolCall], list[ToolCall]]:
    pairs: list[tuple[ToolCall, ToolCall]] = []
    new_left = list(new)
    missing_left: list[ToolCall] = []
    for call in missing:
        match = next((i for i, r in enumerate(new_left) if r.tool == call.tool), None)
        if match is None:
            missing_left.append(call)
        else:
            pairs.append((call, new_left.pop(match)))
    return pairs, missing_left, new_left


def output_similarity(golden: str, replay: str) -> float:
    """``difflib`` ratio of the two final outputs with whitespace collapsed and case folded,
    1.0 when both are empty. A model-free signal: below ``cascade.OUTPUT_SIMILARITY_FLOOR``
    the Nano screen alone may not clear the pair (ADR 0024)."""
    a, b = (re.sub(r"\s+", " ", t).strip().lower() for t in (golden, replay))
    if not a and not b:
        return 1.0
    return round(SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)


def diff(golden: Trace, replay: Trace, unexpected: list[ToolCall]) -> Level0Finding:
    golden_calls = side_effect_calls(golden)
    replay_calls = _merge_unexpected(side_effect_calls(replay), unexpected)
    missing, new = _cancel_exact(golden_calls, replay_calls)
    changed, missing_calls, new_calls = _pair_changed(missing, new)
    return Level0Finding(
        golden_run_id=golden.run_id,
        replay_run_id=replay.run_id,
        new_calls=new_calls,
        missing_calls=missing_calls,
        changed_args=changed,
        hard_fail=any(c.side_effect in HARD_SIDE_EFFECTS for c in new_calls),
        missing_hard=any(c.side_effect in MISSING_HARD for c in missing_calls),
        output_similarity=output_similarity(golden.final_output, replay.final_output),
    )
