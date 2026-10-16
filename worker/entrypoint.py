"""ratchet-worker container entrypoint: ``python worker/entrypoint.py --inputs /in --output /out``.

A thin wrapper so the image's ENTRYPOINT does not depend on module paths; all behaviour
(exit codes 0/3/4/5/6, result.json, progress.json) lives in ``ratchet.replay.worker``.
"""

from __future__ import annotations

from ratchet.replay.worker import cli

__all__: list[str] = []

if __name__ == "__main__":
    raise SystemExit(cli())
