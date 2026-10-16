"""World overlays: recomputed world-dependent cassettes under the golden's key (design §5)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import SCENARIOS, fixture_trace, make_settings, open_store  # noqa: E402

from ratchet.mocktools.normalize import cassette_key, normalize_args  # noqa: E402
from ratchet.models import Cassette, ToolCall  # noqa: E402
from ratchet.orchestrator import overlay  # noqa: E402

ARGS = {"base": "EUR", "quote": "USD", "date": "2026-10-14"}


def test_overlay_shadows_the_fx_cassette_with_the_moved_rate(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    norm = normalize_args("finance.rate", ARGS)
    key = cassette_key("finance.rate", norm)
    golden = fixture_trace("d1_fx_drift", run_id="run_fx")
    steps = [
        s.model_copy(update={"cassette_key": key, "args_norm": norm})
        if isinstance(s, ToolCall) and s.tool == "finance.rate"
        else s
        for s in golden.steps
    ]
    golden = golden.model_copy(update={"steps": steps})
    store.save_cassette(
        key,
        Cassette(
            tool="finance.rate",
            args_norm=norm,
            response={"result": {"rate": 1.08}},
            recorded_at=golden.created_at,
            side_effect="read",
        ),
    )
    root = overlay.build(
        store, settings, "3", SCENARIOS / "3" / "world", [golden], base_world=SCENARIOS / "world"
    )
    shadow = store.load_cassette("finance.rate", key, root)
    assert shadow is not None and "1.163" in str(shadow.response)
    main = store.load_cassette("finance.rate", key)
    assert main is not None and main.response == {"result": {"rate": 1.08}}
    assert store.load_cassette("sheets.append", key, root) is None  # not world-dependent
