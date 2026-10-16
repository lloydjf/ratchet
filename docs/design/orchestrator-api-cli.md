# Design: orchestrator, Simulate, dashboard API, CLI, `ratchet serve`

How the orchestrator, Simulate, the dashboard API, the CLI and `ratchet serve` fit together.
This layer wires the components together. It never re-implements them: call the judge, replay,
watcher, store and capture APIs as they exist on disk. When an existing API is missing
something, add it in the owning module with a small, tested change.

## 1. Modules

```
ratchet/orchestrator/
  context.py     EditContext: resolved repo, runner, judge client, tavily source, cascade config, session
  pipeline.py    per-edit state machine (§3); one async handler per state; restartable
  replaying.py   selecting/dispatching/collecting: selection → batches → pack → submit → poll → import
  judging.py     judging/drifting: level0 + judge_pair (bounded concurrency) + drift.check_pair; pair.* events
  deciding.py    deciding/acting/notifying: decide → narrative → Verdict → git action → notify
  service.py     Orchestrator: FIFO queue, max in flight, resume on start, watcher hookup, stall detection,
                 simulate auto-reset loop, weekly digest schedule
  progress.py    Progress view derived from events (stage labels via ratchet.copy)
  bless.py       bless(run_id), rebless(golden, replay), unbless(run_id), set_criteria
  actions.py     manual rollback / approve (per-verdict lock, idempotent, events, toasts)
  simulate.py    scenario catalog, session repos, seeded edit, world overlay, replayed mode, auto-reset
  agent.py       run a live task on the control plane (record mode) → Trace(kind=live)
  demo.py        seed the demo: skills repo from scenarios/skills, record + bless the 25 goldens
  digest.py      weekly digest (Ultra) → data/digests/<id>.json + digests row + digest.written
  overlay.py     world overlay: world-fixture overrides → cassette overlay for the goldens' world tools
ratchet/dashboard_api/   app.py, deps.py, errors.py, security.py (action token, rate limit, session cookie),
                         views.py, sse.py, static.py, routes/{meta,simulate,verdicts,edits,traces,costs,
                         digest,events,demo,health}.py
ratchet/cli/main.py      Typer app (the CLI reference + serve, seed-demo, verify-live, metrics, digest,
                         db migrate, schemas export, api export-openapi)
ratchet/serve.py         run dashboard (8080), proxy (8081), mock tools (8082, record mode, demo world),
                         orchestrator + watcher in one process (dev / docker-compose `control`)
```

## 2. Identity and ids

- `verdict_id` is pre-allocated from the edit: `edit_<hex>` gives `verdict_<hex>`. The API can
  return it when Simulate starts.
- The judge client for an edit is `ModelClient(run_id=f"judge_{hex}", edit_id, session_hash)`.
  Every judge call is therefore costed under the edit, and the cost panel is
  `judge.costs.verdict_costs(store, edit_id)`. Replay agent calls carry `X-Ratchet-Edit-Id`
  too (the worker sidecar sets it from the batch spec).
- Every event, edit, verdict and cost row created for a Simulate run carries `session_hash`.

## 3. State machine (persisted in `edits.state`)

`detected → selecting → dispatching → collecting → judging → drifting → deciding → acting →
notifying → done`, plus `failed_infra` (WARN `replay_infra`, held). Each handler:

1. Reads its inputs from the store and is idempotent. If a handler crashes, re-running it
   reproduces the same result, and persisted artifacts are reused rather than recomputed
   (judged pairs saved in `edits/<id>/pairs/<golden>.json`).
2. Writes `edits.state` *before* doing work, and emits `orchestrator.state {from, to,
   edit_id, elapsed_ms}`.
3. Records a `StageTiming`. The verdict's `stages` list comes from these.

| State | Work | Events |
| --- | --- | --- |
| selecting | `select_traces(store, repo, edit, embedder, cap, threshold=settings.ratchet_neighborhood_threshold)`; 0 traces → deciding with PASS `no_affected_traces` | `edit.selected` (`selection_counts`) |
| dispatching | `make_batches`, `checkout_tar(edit.commit)` once per edit, `pack_batch_inputs`, `runner.submit` (idempotent by batch_id), persist job ids; submit failure after retries → failed_infra | `batch.submitted` |
| collecting | poll each job (2 s); emit `batch.heartbeat {k, n}` when progress changes; no heartbeat for 120 s → stalled: cancel, retry once on the local runner; terminal → `import_batch_output`; failed goldens → `PairVerdict(outcome="failed", decided_by="infra")` | `batch.heartbeat`, `batch.finished` |
| judging | per pair: `level0.diff` → `judge_pair` (bounded by an asyncio semaphore, 8) | `pair.level0`, `pair.opinion` (one per opinion), `pair.decided` |
| drifting | for pairs with `golden.world_dependent` and outcome regression/uncertain, when `settings.ratchet_feature_drift` → `drift.check_pair` | `pair.drift` (one per check), `pair.decided` again if changed |
| deciding | `decide(...)`; `verdict_costs`; `write_narrative(NarrativeInput(...))`; assemble and save the `Verdict` (headline, tokens, counterfactual, stages, simulated_models=settings.simulated, session, affected_skills, author, runner, batch_ids) | `verdict.decided` |
| acting | PASS → `repo.merge_candidate(verdict_id, commit=edit.commit)`; BLOCK → `repo.revert(edit.commit)`; WARN → hold. `ConflictError(merge_conflict)` on merge → WARN `merge_conflict`, held (verdict re-saved). Store `action_commit`. | `verdict.acted` |
| notifying | `notify(build_payload(verdict, edit, public_url))` (never raises) + toast event | — |

Queueing: an edit whose `affected_skills` intersect an edit already in flight waits. The
service runs at most `settings.ratchet_max_edits_in_flight` edits at once, FIFO. On
start-up, every edit in a non-terminal state resumes.

## 4. Repos and sessions

- **Operator repo:** `SkillsRepo(settings.ratchet_skills_repo)`, created by `ratchet seed-demo`
  (`demo.seed_demo`, which initializes the repo when it does not exist yet); there is no
  `ratchet init` command. The Watcher runs on it and `on_edit` calls `orchestrator.submit_edit(edit,
  diff)`, which runs `store.save_edit`, emits `edit.detected` and enqueues the edit.
- **Simulate session repo:** `data/sessions/<session_hash>/skills`, created with
  `SkillsRepo.clone_to` from the operator repo's `main`. `apply_files` writes the scenario's
  `edit/` files as a `seeded` commit, and `build_edit(..., author="seeded",
  session_hash=...)` builds the edit. `EditContext` resolves the repo from
  `edit.session_hash`.
- **Auto-reset:** a loop every 30 s deletes the session dirs and marks the sessions `reset`
  once `reset_at` has passed (10 min). The verdict JSON stays readable, so deep links keep
  working.
- **Concurrency:** at most `ratchet_simulate_max_sessions` running sessions. Beyond that the
  API answers `status="queued"` with `queue_position` and `eta_s`.
- **Re-runs:** a session that presses the same scenario again gets a fresh edit.
- **Public mode:** bless and record are refused (403 `not_public`). Rebless inside a session
  writes a session-scoped golden copy marked with the session and never touches the shared
  library. The shared library is the operator's.

## 5. Scenarios on the demo host

`simulate.py` loads `scenarios/<k>/scenario.yaml`. Before dispatch, scenario 3 builds a world
overlay with `overlay.build`. For each world-dependent ToolCall in the selected goldens, it
recomputes the response with the overlay world and the same args, and writes a cassette under
`data/overlays/<scenario>/` with the same key. The batch's `world_ref` points there.

The Tavily source for an edit is chosen as follows:
- `settings.ratchet_tavily_mode == "live"` and a key is set: `LiveTavily`.
- Otherwise `FixtureTavily`, over the scenario's `tavily/` dir when the edit belongs to a
  scenario, else `settings.ratchet_tavily_fixtures_dir`.

**Replayed mode** (`RATCHET_SIMULATE_MODE=replayed`, used on the public demo host): `ratchet simulate k
--record-fixture` stores a finished scenario run under `scenarios/recorded/<k>/`. That
covers the verdict, edit, diff, the pair traces it references, and its events with their
relative timestamps. In replayed mode, `POST /simulate/k` copies that record under fresh ids
with `recorded=True` and the session hash. It then re-emits the events at their original
relative times, compressed ×2, so the strip still moves. No model is called and nothing is
spent.

## 6. Demo seeding and golden recording

`ratchet seed-demo` (idempotent) does four things:
1. Initializes the operator skills repo from `scenarios/skills/`.
2. Loads `scenarios/golden/tasks.yaml`.
3. For each task, runs `agent.run_task` on the control plane through an in-process proxy
   (live or sim, per settings) and mock tools in record mode against `World.load(
   "scenarios/world")`, using the MinimalRunner, frozen time from the world, and seed =
   stable hash of the task_id.
4. Blesses the result with `bless.bless` (PII pass → `judge.bless.enrich` → kind golden).

Its own canary list is the three `canary: true` tasks; `select_traces(canaries=...)` gets them.

**Mind the cost.** On live models, recording 25 goldens is about 25 × 10k tokens on super,
roughly $0.10. `--force` re-records.

`agent.run_task(task_input, *, settings, store, repo_dir, world, frozen_time, seed,
task_label) -> Trace` is the operator path too (`ratchet agent run "<task>"`). It:
1. Snapshots the workspace before the task: `workspace.tar.zst`, the `env.workspace_ref`.
2. Writes the run meta.
3. Runs the adapter with `proxy_url=<proxy>/r/<run_id>` and the mock tools URL.
4. Finalizes with `finalize_trace(kind="live")`.

## 7. Bless / rebless

- `bless(store, run_id, client)`: requires kind `live`. It runs the PII tokenizer over
  `task_input`/`final_output` and saves the map, then `judge.bless.enrich`, then sets
  kind=golden and `blessed_at`, saves, and emits `trace.blessed {run_id, skills_invoked,
  world_dependent, n_facts}`.
- `rebless(golden, replay)`: copies the replay trace dir to a new run id with kind=golden,
  `golden_of=None`, a new `blessed_at`, world facts re-extracted from the new output, and
  `env` copied from the old golden. For world tools that the replay served from an overlay,
  it promotes those overlay cassettes into the main cassette store. It archives the old
  golden with `archived_reason="drift"`, emits `pair.reblessed`, and returns both ids.

## 8. Dashboard API (shapes in docs/CONTRACTS.md)

- `create_app(settings=None, store=None, orchestrator=None) -> FastAPI`. Routes live under
  `/api/v1`, with `/api` as an alias (ADR 0019). The built SPA is served from
  `settings.ratchet_dashboard_dist` with history-API fallback to `index.html`, under a
  `Cache-Control` policy of immutable for hashed assets and no-cache for index.
  `/healthz` returns `{ok, components}` and `/metrics` is Prometheus format (edit and
  simulate metrics).
- **Errors:** one handler maps `RatchetError` to `{"error": {code, message, request_id,
  details?}}`, and `X-Request-Id` is echoed.
- **Session:** the `ratchet_session` cookie (HttpOnly, SameSite=Lax, Secure when the
  public URL is https, 24 h) is created on first `/meta` or `/simulate`. The session hash is
  sha256(cookie)[:16].
- **Action token:** `X-Ratchet-Action-Token` = HMAC(secret, UTC date)[:24], so it rotates
  daily. The previous day's token is accepted for 1 h after midnight. It is required
  on POST actions when `public_mode`, and its value is served in `/meta`.
- **Rate limit:** 10 actions per minute per client IP, in memory (token bucket). The IP is
  used only for the bucket and never stored.
- **SSE:** `GET /api/v1/events/stream` uses `sse-starlette`. It filters to the session plus
  global events, honors the `since` query or the `Last-Event-ID` header, polls the events
  table every 250 ms, sends a heartbeat comment every 15 s, and stops cleanly when the client
  disconnects.
- **Verdict view:** `GET /verdicts/{id}` for a running edit returns `verdict=null` plus
  `progress` from `progress.py`. Every response is built from stored JSON; no model is called.
- **Costs:** `today`, `week`, `last_edit` tiles, `by_purpose`, `by_day`, `counterfactual`.
- **Digest:** `/digest/latest` returns 404 with copy until a digest exists. `ratchet digest
  run` creates one.
- **Demo events:** `POST /demo/events` validates kind against the allowed funnel-event list and stores rows
  in `demo_events` with only the allowed fields. It never stores an IP or a user agent; the
  client sends only a device class.
- **OpenAPI:** `ratchet api export-openapi > schemas/openapi.json`. The model JSON schemas
  go to `schemas/*.json` via `ratchet schemas export`, and a unit test fails when they drift from the models.

## 9. CLI (Typer, `ratchet = ratchet.cli.main:main`)

| Command | Behavior |
| --- | --- |
| `doctor` | Checks settings, keys, model ids (live: `GET /models` through the upstream), runner, sandbox, Tavily, git worktrees, disk, caps. Prints a table and exits non-zero on failure. |
| `serve [--no-watch]` | All services in one process. |
| `seed-demo [--force]` | See §6. |
| `agent run "<task>"` / `agent start --demo` | `start --demo` runs the seeded demo agent's next task. |
| `bless <run_id> \| --last \| --all-demo` | |
| `status [--timing <verdict_id>]` | |
| `replay <edit_id> [--runner local\|nebius]` | |
| `rollback`, `approve`, `rebless` | |
| `simulate <k> [--headless] [--record-fixture]` | Headless prints the stages, then the verdict headline. Exit code 0 if the decision matches `expected_decision`. |
| `purge --before <date> [--include-golden]` | |
| `logs --edit <id>` | |
| `metrics [--since 7d] [--demo]` | Product-metric and demo-funnel tables. |
| `digest run` | |
| `db migrate` | |
| `schemas export` | |
| `api export-openapi` | |
| `verify-live` | Live checks with real keys, about $0.05: model ids, `seed` repeatability (5 calls), `json_schema` per tier, thinking toggle, `include_usage`, Tavily `time_range`. Appends the results to docs/VERIFIED.md "Live results". |

## 10. Tests

- End-to-end: a cosmetic edit → PASS → merged; an R4 edit → BLOCK → reverted (`main`
  unchanged, notification sent); an R2 edit → BLOCK via the cascade, headline names the
  field; a D1 world change → drift → PASS with Re-bless offered; and simulate 1–3 cold (each
  < 60 s locally, auto-reset). All run in-process with `RATCHET_UPSTREAM=sim`, the local process
  runner and the process sandbox.
- Restart resume: kill mid-state, restart, same verdict.
- Failure cases where they cross the orchestrator: a worker killed mid-batch (unfinished
  pairs fail, one retry round), a judge tier unavailable (opinions `uncertain`, a verdict is
  still produced), Tavily down (drift `unavailable`, never a PASS), `main` moved under the
  edit (WARN `merge_conflict`), and a corrupted cassette.
- API tests for every route, including the action token, rate limit, session isolation
  (two sessions never see each other's events), `not_public`, and SSE `Last-Event-ID` resume.
- CLI smoke tests with `typer.testing.CliRunner`.

## 11. Pinned interface between the orchestrator and the dashboard API

The dashboard API codes against exactly these names, and the orchestrator implements them.
Both live in `ratchet/orchestrator/`.

```python
# service.py
class Orchestrator:
    def __init__(self, settings: Settings, store: Store) -> None: ...
    async def start(self, *, watch: bool = True) -> None      # resume non-terminal edits, start loops
    async def stop(self) -> None
    def submit_edit(self, edit: SkillEdit, diff: str) -> None  # save_edit + edit.detected + enqueue
    async def wait_idle(self, timeout_s: float = 300) -> None  # tests
    async def replay(self, edit_id: str) -> None               # POST /edits/{id}/replay: reset state to selecting

# progress.py
class StageView(BaseModel): key: str; label: str; status: Literal["pending","active","done","skipped"]; detail: str = ""
class Progress(BaseModel):
    stage: str; stage_label: str; k: int; n: int; elapsed_s: float
    usd_by_tier: dict[Tier, float]; stalled: bool; stages: list[StageView]
def progress_for(store: Store, edit_id: str) -> Progress | None   # None once the edit is done

# simulate.py
class ScenarioInfo(BaseModel): scenario_id: str; skill: str; title: str; learned: str; catches: str; expected_decision: Decision
class SimulateStart(BaseModel):
    session_hash: str; scenario_id: str; edit_id: str | None; verdict_id: str | None
    status: Literal["running", "queued", "done"]; queue_position: int | None = None; eta_s: int | None = None
class SimulateService:
    def __init__(self, settings: Settings, store: Store, orchestrator: Orchestrator) -> None: ...
    def scenarios(self) -> list[ScenarioInfo]
    async def start(self, scenario_id: str, session_hash: str) -> SimulateStart  # NotFoundError for an unknown id
    async def reset_expired(self) -> int

# actions.py   (per-verdict lock; ConflictError(action_locked); events verdict.rolled_back / verdict.acted)
async def rollback(store: Store, settings: Settings, verdict_id: str, *, actor: str) -> Verdict
async def approve(store: Store, settings: Settings, verdict_id: str, *, actor: str) -> Verdict

# bless.py     (ForbiddenError(not_public) when settings.ratchet_public_mode, except a session-scoped rebless)
async def bless(store: Store, settings: Settings, run_id: str) -> Trace
async def rebless(store: Store, settings: Settings, golden_run_id: str, replay_run_id: str,
                  *, session_hash: str | None = None) -> tuple[str, str]   # (new_golden, archived)
def unbless(store: Store, settings: Settings, run_id: str) -> Trace
def set_criteria(store: Store, settings: Settings, run_id: str, criteria: list[str]) -> Trace

# digest.py
class Digest(BaseModel):
    digest_id: str; period_start: datetime; period_end: datetime; narrative: str
    verdict_ids: list[str]; merged: list[str]; blocked: list[str]; usd: float; simulated_models: bool
def latest(store: Store) -> Digest | None
async def run_digest(store: Store, settings: Settings, *, days: int = 7) -> Digest
```

The verdict-id/edit-id helpers live in `ratchet/orchestrator/ids.py`: `verdict_id_for(edit_id)`
and `judge_run_id(edit_id)`.
