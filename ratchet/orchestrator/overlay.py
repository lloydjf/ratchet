"""World overlays: "the world moved" for a scenario's world-dependent tools (design §5).

A scenario's ``world/`` directory holds world-fixture overrides (``fx.json``, …; same format
as ``ratchet/mocktools/WORLD_FORMAT.md``) laid over the base demo world. For every
world-dependent ``ToolCall`` the goldens were served from a cassette, the response is
recomputed against the overlay world with the same (normalized) args and written as a
cassette under ``data/overlays/<scenario>/`` with the *same* cassette key. A replay batch
whose ``world_ref`` points there is served those responses first (the mock tool server
consults the overlay before the main cassettes for world-dependent tools only).
Ready-made cassettes in ``world/<tool>/<key>.json`` are copied in as they are.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.errors import RatchetError
from ratchet.log import get_logger
from ratchet.mocktools.adapters import get_adapter
from ratchet.mocktools.world import WORLD_FILES, CallContext, World
from ratchet.models import Cassette, ToolCall, Trace
from ratchet.pii import PiiTokenizer
from ratchet.store import Store
from ratchet.tools.catalog import get_tool

__all__ = ["overlay_dir", "merged_world", "build", "copy_cassettes"]

_CASSETTE = re.compile(r"^[0-9a-f]{64}\.json$")
_log = get_logger("overlay")


def overlay_dir(store: Store, scenario_id: str) -> Path:
    return store.data_dir / "overlays" / scenario_id


def merged_world(base: Path, override: Path, dest: Path) -> World:
    """Load ``base`` with ``override``'s world files (and receipts) laid on top."""
    shutil.copytree(base, dest, dirs_exist_ok=True)
    for name in WORLD_FILES:
        if (override / name).is_file():
            shutil.copyfile(override / name, dest / name)
    if (override / "receipts").is_dir():
        shutil.copytree(override / "receipts", dest / "receipts", dirs_exist_ok=True)
    return World.load(dest)


def copy_cassettes(override: Path, dest: Path) -> int:
    copied = 0
    for path in sorted(override.glob("*/*.json")):
        if _CASSETTE.match(path.name) and path.parent.name not in {"receipts"}:
            target = dest / path.parent.name / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            copied += 1
    return copied


def _recompute(
    store: Store, world: World, golden: Trace, step: ToolCall, pii: PiiTokenizer
) -> Cassette | None:
    spec, adapter = get_tool(step.tool), get_adapter(step.tool)
    if spec is None or not spec.world_dependent or adapter is None or not step.cassette_key:
        return None
    original = store.load_cassette(step.tool, step.cassette_key)
    if original is None:
        return None
    ctx = CallContext(run_id=f"overlay-{golden.run_id}", now=golden.env.frozen_time)
    try:
        result = adapter(world.state(ctx.run_id), dict(original.args_norm), ctx)
        response: dict[str, object] = {"result": pii.tokenize_obj(result)}
    except RatchetError as exc:
        response = {"error": {"code": exc.code, "message": exc.message}, "status": exc.http_status}
    return original.model_copy(
        update={"response": response, "recorded_at": clock.now(), "later_responses": []}
    )


def build(
    store: Store,
    settings: Settings,
    name: str,
    override: Path,
    goldens: list[Trace],
    *,
    base_world: Path,
) -> Path:
    """Write the overlay cassettes for ``goldens`` under ``data/overlays/<name>``."""
    dest = overlay_dir(store, name)
    dest.mkdir(parents=True, exist_ok=True)
    copied = copy_cassettes(override, dest)
    pii = PiiTokenizer.for_settings(settings)
    written = 0
    with tempfile.TemporaryDirectory(prefix="ratchet-overlay-") as tmp:
        world = merged_world(base_world, override, Path(tmp) / "world")
        for golden in goldens:
            for step in golden.steps:
                if not isinstance(step, ToolCall) or step.cassette_key is None:
                    continue
                cassette = _recompute(store, world, golden, step, pii)
                if cassette is not None:
                    store.save_cassette(step.cassette_key, cassette, root=dest)
                    written += 1
    pii.save()
    _log.info("overlay.built", overlay=name, n_written=written, n_copied=copied)
    return dest
