"""Record-mode tool adapters over the fictional world, one per non-local catalog tool.

``ADAPTERS`` maps a dotted tool name to ``adapter(state, args, ctx) -> result``. Adapters
are pure functions of the run's :class:`~ratchet.mocktools.world.WorldState` (which they
may mutate for write tools) and raise ``RatchetError`` subclasses for tool-level errors
(``not_found`` 404, ``bad_args`` 400, conflicts 409), which are recorded like results.
"""

from __future__ import annotations

from ratchet.mocktools.adapters import comms, github, info, money, schedule
from ratchet.mocktools.adapters.base import Adapter

__all__ = ["ADAPTERS", "Adapter", "get_adapter"]

ADAPTERS: dict[str, Adapter] = {
    **github.ADAPTERS,
    **comms.ADAPTERS,
    **money.ADAPTERS,
    **schedule.ADAPTERS,
    **info.ADAPTERS,
}


def get_adapter(tool: str) -> Adapter | None:
    return ADAPTERS.get(tool)
