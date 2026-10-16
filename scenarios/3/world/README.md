# Scenario 3 world overlay: "the world moved"

These files are **world-fixture overrides** in `ratchet/mocktools/WORLD_FORMAT.md` format.
Each file here replaces the file of the same name in `scenarios/world/`; everything else
stays as it is. The only override is `fx.json`: EUR/USD on 2026-10-14 is `1.1630` instead
of the `1.0800` the goldens were recorded with. The skill change in this scenario is
cosmetic, so every difference in the replays comes from this one fact. All values are
synthetic.

## How it becomes a cassette overlay

Replays never read the world; they read cassettes. The worker passes
`ReplayBatch.world_ref` to the mock tool server as `overlay_root`, a cassette root that is
consulted first for world-dependent tools (`finance.rate`, `web.*`, `time.now`). To build
it:

1. Copy `scenarios/world/` to a temporary directory and copy this directory's files over
   it (`fx.json` replaces the base `fx.json`).
2. For each of the 4 fx-expense-converter goldens, take the golden's `finance.rate`
   ToolCall args (`{"base": "EUR", "quote": "USD", "date": "2026-10-14"}`), run the
   `finance.rate` adapter against the overlaid world, and write the result as a cassette at
   `<overlay_root>/finance.rate/<cassette_key>.json`, where the key is
   `ratchet.mocktools.normalize.cassette_key("finance.rate", args_norm)` — the same key as
   the golden's cassette, so the overlay shadows it.
3. Point the scenario's `world_ref` at that overlay root. Only `finance.rate` changes;
   `sheets.append` and every other tool still replay from the golden cassettes, so the
   replay's `sheets.append` rows carry the new USD figures as `changed_args`, which is not
   a hard failure.

The Tavily fixtures that show the new rate live in `scenarios/3/tavily/` (also mirrored in
`tests/fixtures/tavily/scenario3/` for tests).
