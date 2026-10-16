"""Offline simulator: a deterministic stand-in for Nebius Token Factory (ADR 0018).

``RATCHET_UPSTREAM=sim`` routes the capture proxy here. Agent calls are answered by scripted
skill policies (:mod:`ratchet.sim.agent`), judge calls by heuristics over the rendered prompt
sections (:mod:`ratchet.sim.judge`). Everything this package produces is *test
infrastructure*: it is never Nemotron output, and no README or video number may come from it.
"""

from __future__ import annotations

__all__: list[str] = []
