# Configuration

Ratchet has one settings object, `ratchet/config/settings.py`. Every field maps 1:1 to an
environment variable with the same name in upper case (`ratchet_daily_usd_cap` is
`RATCHET_DAILY_USD_CAP`). Names are case-insensitive. Values come from the process
environment first, then from `.env` in the working directory. Ratchet validates them at
start-up, and a bad value fails fast with the field's name.

`.env.example` is the starting point: `cp .env.example .env`. It lists the settings most
installs change. Every other setting keeps the default shown below.

!!! tip "See what a running instance uses"
    `uv run ratchet doctor` prints the upstream, data directory, runner, sandbox, Tavily
    mode, git state, disk and today's spend against the cap. The dashboard's
    [Settings page](dashboard.md#settings) shows the harness, runner, sandbox, model ids and
    caps read-only. Change values in `.env` and restart Ratchet.

## The settings you will change first

| You want to | Set |
| --- | --- |
| Run fully offline, with no keys | `RATCHET_UPSTREAM=sim`, `RATCHET_TAVILY_MODE=fixtures` (the defaults) |
| Use real Nemotron models | `NEBIUS_TOKEN_FACTORY_KEY=…`, `RATCHET_UPSTREAM=live` |
| Use live Tavily for drift checks | `TAVILY_API_KEY=…`, `RATCHET_TAVILY_MODE=live` |
| Keep spend low | `RATCHET_DAILY_USD_CAP`, `RATCHET_PURPOSE_USD_CAPS` |
| Isolate replays properly | `RATCHET_SANDBOX=docker` (or `openshell` on a suitable Linux host) |
| Get notified about verdicts | `RATCHET_NOTIFY_WEBHOOK=https://…` |
| Serve a public demo | `RATCHET_PUBLIC_MODE=true`, a long random `RATCHET_ACTION_TOKEN_SECRET` and `RATCHET_PUBLIC_URL` (see [Public mode](#public-mode)) |
| Measure reliability without the cache | `RATCHET_CACHE_TTL_DAYS=0` |

## Budget caps

The capture proxy checks the caps before it forwards every model call. Once spend so far
has reached a cap, the call is refused with `429 budget_exceeded`.

| Cap | Setting | Default | Scope |
| --- | --- | --- | --- |
| Daily, all purposes | `RATCHET_DAILY_USD_CAP` | $10 | resets at 00:00 UTC |
| Per purpose, daily | `RATCHET_PURPOSE_USD_CAPS` | none | JSON map, resets at 00:00 UTC |
| Benchmark | `RATCHET_BENCH_USD_CAP` | $12 | all-time hard cap on the `bench` purpose |

Purposes are `agent`, `screen`, `check`, `adjudicate`, `extract`, `narrative`, `bench` and
`embed`. For example, `RATCHET_PURPOSE_USD_CAPS={"agent": 5, "adjudicate": 3}` caps agent
replays at $5 a day and Ultra adjudication at $3 a day, inside the $10 total.

What happens when a cap is reached:

- The edit is held, not passed. The dashboard says "Spending cap reached for today. The
  edit is held; raise the cap in Settings or wait until tomorrow." A pair that could not be
  judged shows "Paused: spending cap reached."
- Replay workers get a dispatch-time allowance: the remaining daily, per-purpose and bench
  dollars, split across the batches of the dispatch. The worker's sidecar proxy enforces
  it on its own spend (ADR 0022).
- `ratchet doctor` warns at 80 % of the daily cap. On the demo host, an alert fires when
  spend rises by more than $5 in an hour (see [Operations](operations.md#metrics-and-alerts)).
- `ratchet bench build` and `ratchet bench run` print an estimate first. They refuse to start
  when it exceeds what is left of the bench cap ([Benchmark](benchmark.md#costs)).

Model prices live only in `ratchet/config/pricing.py`, and model ids only in
`ratchet/config/models.py`. You can override the ids with `RATCHET_MODEL_NANO`,
`RATCHET_MODEL_SUPER` and `RATCHET_MODEL_ULTRA`.

## Public mode

`RATCHET_PUBLIC_MODE=true` turns a host into a public demo. It changes this:

- **Blessing and recording are off.** `POST /runs/{id}/bless`, unbless and criteria edits
  answer `403 not_public`, and so do the golden library, the trace list and the Settings
  view. Re-bless inside a Simulate session writes a session-scoped copy and never touches the
  shared library.
- **Actions need the action token.** Every `POST` action (Simulate, rollback, approve,
  re-bless, replay) needs the `X-Ratchet-Action-Token` header. The token is
  `HMAC-SHA256(RATCHET_ACTION_TOKEN_SECRET, UTC date)`, truncated to 24 hex characters.
  It rotates daily, and yesterday's token is still accepted until 01:00 UTC. The
  dashboard reads it from `GET /api/v1/meta`.
- **The secret must not be the default.** `ratchet serve` refuses to start in public mode
  when `RATCHET_ACTION_TOKEN_SECRET` is the shipped default or shorter than 16 characters.
- **The `process` sandbox is refused** (ADR 0017). `ratchet doctor` fails the sandbox check.
- **Actions are owner-only.** On a public host you can only act on verdicts from your own
  Simulate session. Sessions use the `ratchet_session` cookie.

The public host also enforces these limits:

| Limit | Setting | Default |
| --- | --- | --- |
| Actions per minute per client IP | fixed | 10 (`429 rate_limited`) |
| Concurrent Simulate sessions | `RATCHET_SIMULATE_MAX_SESSIONS` | 5; more are queued |
| Queued Simulate runs | `RATCHET_SIMULATE_MAX_QUEUE` | 20, then `429 simulate_busy` |
| Simulate runs per client IP | fixed | 1 running or queued, then `429 simulate_busy` |
| SSE streams per client IP / in total | `RATCHET_SSE_MAX_PER_CLIENT` / `RATCHET_SSE_MAX_STREAMS` | 3 / 100 |
| Request body size | `RATCHET_API_MAX_BODY_BYTES` | 64 KiB (`413 payload_too_large`) |
| Scenario auto-revert | `RATCHET_SIMULATE_RESET_S` | 600 s |

Rate limits are keyed by client IP. Behind a reverse proxy, set
`RATCHET_FORWARDED_ALLOW_IPS` to the proxy's address, or every client shares one bucket.
The counters live in memory, so a restart resets them. The daily USD cap is stored in
SQLite and survives restarts.

## Runners, sandboxes and harnesses

A replay has three moving parts: where the worker runs (the **job runner**), what isolates
the agent (the **sandbox**), and which agent runtime executes the skill (the **harness**).

| Setting | Value | Where it works | Status |
| --- | --- | --- | --- |
| `RATCHET_JOB_RUNNER` | `local` (default) | the control-plane host | Default, and used for the judge-facing demo, because Jobs cold starts take minutes (ADR 0011) |
| | `nebius` | Nebius Serverless Jobs; batch inputs and outputs travel through an Object Storage bucket | Built. Needs `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID` and `NEBIUS_S3_BUCKET`. Some API field names are still unverified live |
| `RATCHET_LOCAL_RUNNER_MODE` | `process` (default) | the local runner starts the worker as a subprocess | Development |
| | `docker` | the local runner starts the `RATCHET_WORKER_IMAGE` container | Needs Docker |
| `RATCHET_SANDBOX` | `process` (default) | any OS | **Development only.** No isolation and no egress enforcement. Refused in public mode and with the Hermes harness (ADR 0017) |
| | `docker` | Linux or macOS with Docker | Locked-down container on the internal `ratchet-sandbox` bridge: `--cap-drop ALL`, read-only root. Used inside Serverless Jobs (ADR 0010) |
| | `openshell` | Linux ≥ 6.2 with Landlock ABI 3 and Docker ≥ 28 or Podman 5 | Deny-all policy except the proxy and mock tools. The intended production path on the VM. Not yet verified live |
| `RATCHET_HARNESS` | `minimal` (default) | everywhere | The in-repo MinimalRunner. Every published number was produced with it (ADR 0012) |
| | `hermes` | `docker` or `openshell` sandbox, with Hermes Agent installed | Adapter built. Passes its acceptance checks against a real Hermes v0.22.2 install on the offline simulator; no live-model run yet |

!!! warning "Which combination to use"
    - **Laptop, first run:** `local` + `process` + `process` + `minimal` (the defaults). Fine for
      the seeded demo and your own experiments. Don't point it at a skill you don't trust.
    - **Your own agent, day to day:** `local` + `docker` runner mode + `docker` sandbox +
      `minimal`. Create the sandbox network with `deploy/sandbox-network.sh`.
    - **Public demo host:** `docker` or `openshell` sandbox. `process` is refused.

## Every setting

<!-- BEGIN GENERATED by scripts/gen_settings_docs.py -->

!!! info "Generated section"
    Generated by `scripts/gen_settings_docs.py`. Do not edit it by hand: change the source,
    then run `uv run python scripts/gen_docs.py`.

All 84 settings, in the order and groups of `ratchet/config/settings.py`.

### Token Factory (proxy)

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `NEBIUS_TOKEN_FACTORY_KEY` | secret (optional) | unset | Token Factory API key; read by the proxy only, never a sandbox. |
| `NEBIUS_TF_BASE_URL` | string | `https://api.tokenfactory.nebius.com/v1/` | OpenAI-compatible Token Factory base URL (see docs/VERIFIED.md). |
| `RATCHET_MODEL_NANO` | string (optional) | unset | Override the nano model id. |
| `RATCHET_MODEL_SUPER` | string (optional) | unset | Override the super model id. |
| `RATCHET_MODEL_ULTRA` | string (optional) | unset | Override the ultra model id. |
| `RATCHET_MODEL_EMBED` | string (optional) | unset | Override the embedding model id. |
| `RATCHET_UPSTREAM` | `live` \| `sim` | `sim` | live = Token Factory; sim = the in-repo offline simulator (ADR 0018). Verdicts produced by the simulator carry a visible 'Simulated models' badge. |
| `RATCHET_AGENT_TIER` | `nano` \| `super` \| `ultra` | `super` | Tier the agent runs on. |
| `RATCHET_AGENT_THINKING` | `off` \| `on` \| `default` | `off` | Reasoning for agent calls when the harness sends no preference. Nemotron 3 thinks by default; off keeps replays cheap, fast and closer to deterministic. |

### Local service URLs

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `RATCHET_PROXY_URL` | string | `http://127.0.0.1:8081/v1` | Capture proxy. |
| `RATCHET_MOCK_TOOLS_URL` | string | `http://127.0.0.1:8082` | Mock tools. |
| `RATCHET_DASHBOARD_HOST` | string | `127.0.0.1` | Dashboard bind host. |
| `RATCHET_DASHBOARD_PORT` | integer | `8080` | Dashboard + API port. |
| `RATCHET_PROXY_PORT` | integer | `8081` | Proxy port. |
| `RATCHET_MOCK_TOOLS_PORT` | integer | `8082` | Mock tool server port. |

### Storage and skills

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `RATCHET_SKILLS_REPO` | path | `var/skills` | Skills git repo. |
| `RATCHET_DATA_DIR` | path | `var/data` | Store root. |
| `RATCHET_PII_KEY_FILE` | path (optional) | unset | Fernet key for the PII token map; defaults to &lt;data>/../pii.key. |

### Replay

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `RATCHET_JOB_RUNNER` | `nebius` \| `local` | `local` | Job runner. |
| `RATCHET_LOCAL_RUNNER_MODE` | `process` \| `docker` | `process` | How LocalJobRunner launches a worker (ADR 0017). |
| `RATCHET_SANDBOX` | `openshell` \| `docker` \| `process` | `process` | Replay sandbox. process is dev-only; refused in public mode. |
| `RATCHET_HARNESS` | `hermes` \| `minimal` | `minimal` | Harness adapter. |
| `RATCHET_HERMES_VERSION` | string (optional) | unset | Hermes version baked at install time; when unset the adapter runs `hermes --version` once per process. |
| `RATCHET_WORKER_IMAGE` | string | `ratchet-worker:dev` | Worker image. |
| `RATCHET_TF_PROXY_UPSTREAM` | string (optional) | unset | Worker sidecar upstream; defaults to nebius_tf_base_url. |
| `RATCHET_CASSETTE_REFRESH` | boolean | `false` | Overwrite cassettes in record. |
| `RATCHET_BATCH_SIZE` | integer | `10` | Goldens per replay batch. |
| `RATCHET_SIMULATE_BATCH_SIZE` | integer | `4` | Goldens per batch for Simulate runs: more parallel workers, faster. |
| `RATCHET_MAX_TRACES_PER_EDIT` | integer | `40` | Selection cap. |
| `RATCHET_REPLAY_TIMEOUT_S` | integer | `600` | Batch wall clock. |
| `RATCHET_REPLAY_RETRY_ONCE` | boolean | `true` | Re-dispatch replays that crashed (harness/sandbox/worker) once. |

### Nebius Serverless Jobs

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `NEBIUS_API_KEY` | secret (optional) | unset | Nebius IAM token for Jobs. |
| `NEBIUS_PROJECT_ID` | string (optional) | unset | Nebius project (parent) id. |
| `NEBIUS_REGION` | string | `eu-north1` | Nebius region. |
| `NEBIUS_JOBS_API_URL` | string (optional) | unset | Override Jobs API endpoint. |
| `NEBIUS_S3_ENDPOINT` | string (optional) | unset | Object storage endpoint. |
| `NEBIUS_S3_BUCKET` | string (optional) | unset | Bucket for job inputs/outputs. |
| `NEBIUS_S3_ACCESS_KEY_ID` | secret (optional) | unset | Object storage key. |
| `NEBIUS_S3_SECRET_ACCESS_KEY` | secret (optional) | unset | Object storage. |
| `NEBIUS_JOB_PRESET` | string (optional) | unset | Jobs compute preset/platform. |
| `NEBIUS_S3_BUCKET_ID` | string (optional) | unset | Managed bucket id (storagebucket-…) mounted into jobs without inline credentials; NEBIUS_S3_BUCKET stays the bucket name used for uploads. |
| `NEBIUS_JOB_SUBNET_ID` | string (optional) | unset | Jobs subnet; unset uses the project's default subnet. |
| `NEBIUS_JOB_DISK_GIB` | integer (optional) | unset | Job boot disk in GiB; unset sends 20 GiB on cpu-* platforms and the platform default (250 GiB, billed) otherwise. |
| `NEBIUS_JOB_CONTAINER_COMMAND` | string (optional) | unset | Override the worker image entrypoint (e.g. /app/.venv/bin/python); unset keeps the image's own entrypoint and sends only the worker arguments. |
| `RATCHET_JOBS_MAX_CONCURRENT` | integer | `2` | Serverless Jobs in flight at once (each job is a VM on quota). |
| `RATCHET_JOBS_SUBMIT_WAIT_S` | number | `900.0` | How long a job submit waits for a free slot or quota before failing. |
| `RATCHET_JOBS_PROVISION_S` | number | `900.0` | How long a submitted job may provision (VM, image pull) with no worker heartbeat before it counts as stalled; the 120 s heartbeat rule applies once it runs. |

### Tavily

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `TAVILY_API_KEY` | secret (optional) | unset | Tavily key; judge only. |
| `RATCHET_TAVILY_MODE` | `live` \| `fixtures` | `fixtures` | fixtures = recorded responses (bench, offline). |
| `RATCHET_TAVILY_DAILY_SEARCHES` | integer | `300` | Live Tavily searches per UTC day across the host (cache hits are free); past the cap drift checks report unavailable instead of spending more credits. |
| `RATCHET_TAVILY_FIXTURES_DIR` | path | `tests/fixtures/tavily` | Recorded Tavily responses. |

### Judge

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `RATCHET_FEATURE_DRIFT` | boolean | `true` | Tavily drift disambiguation. |
| `RATCHET_FEATURE_CONTROL_REPLAYS` | boolean | `true` | ADR 0023: replay contested pairs once more with the unchanged skill. |
| `RATCHET_FEATURE_RESPONSES_API` | boolean | `false` | /v1/responses passthrough. |
| `RATCHET_THINKING_STYLE` | `chat_template_kwargs` \| `reasoning_effort` \| `none` | `chat_template_kwargs` | How thinking is toggled (docs/VERIFIED.md). |
| `RATCHET_ULTRA_REASONING_BUDGET` | integer | `4000` | Ultra reasoning budget. |
| `RATCHET_STRUCTURED_MODE` | `auto` \| `json_schema` \| `json_object` \| `prompt` | `auto` | Judge response_format mode; auto tries json_schema, then json_object, then prompt-only JSON, remembering the degraded mode per tier (verified: json_schema works live on all tiers; the fallback stays as defence in depth). |
| `RATCHET_SELECTION_EMBEDDER` | `local` \| `tokenfactory` | `local` | Embedding for trigger-neighborhood selection. |
| `RATCHET_NEIGHBORHOOD_THRESHOLD` | number (optional) | unset | Cosine threshold; default depends on the embedder. |
| `RATCHET_CACHE_TTL_DAYS` | integer | `7` | Model response cache TTL. |

### Budget

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `RATCHET_DAILY_USD_CAP` | number | `10.0` | All purposes, per UTC day. |
| `RATCHET_BENCH_USD_CAP` | number | `12.0` | bench purpose hard cap, enforced all-time (cumulative bench spend in the bench store), not per day. |
| `RATCHET_PURPOSE_USD_CAPS` | JSON object | `{}` | JSON, e.g. {"agent": 5, "adjudicate": 3}. |

### Dashboard / public host

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `RATCHET_PUBLIC_URL` | string | `http://127.0.0.1:8080` | Public base URL. |
| `RATCHET_PUBLIC_MODE` | boolean | `false` | Disables bless/record. |
| `RATCHET_SIMULATE_ENABLED` | boolean | `false` | Serve the Simulate HTTP routes (true on the demo host only). `ratchet simulate` on the operator's machine runs regardless. |
| `RATCHET_SIMULATE_MODE` | `live` \| `replayed` | `live` | replayed = stored verdicts, 'Recorded run' badge. |
| `RATCHET_SIMULATE_MAX_SESSIONS` | integer | `5` | Concurrent Simulate sessions. |
| `RATCHET_SIMULATE_RESET_S` | integer | `600` | Scenario auto-revert. |
| `RATCHET_SIMULATE_MAX_QUEUE` | integer | `20` | Queued Simulate runs before 429 simulate_busy. |
| `RATCHET_SSE_MAX_PER_CLIENT` | integer | `3` | Concurrent SSE streams per IP. |
| `RATCHET_SSE_MAX_STREAMS` | integer | `100` | Concurrent SSE streams in total. |
| `RATCHET_API_MAX_BODY_BYTES` | integer | `65536` | Largest request body the API accepts (413 above). |
| `RATCHET_ACTION_TOKEN_SECRET` | secret | `dev-only-change-me` | HMAC secret for the daily action token. |
| `RATCHET_SCENARIOS_DIR` | path | `scenarios` | Seeded scenarios. |
| `RATCHET_DASHBOARD_DIST` | path | `dashboard/dist` | Built SPA. |
| `RATCHET_REPO_URL` | string | `https://github.com/lloydjf/ratchet` | Repo. |
| `RATCHET_VIDEO_URL` | string | empty | Demo video URL for the landing footer. |
| `RATCHET_BENCH_REPORT_URL` | string (optional) | unset | Link to bench/results/&lt;date>/report.md on GitHub (README, Costs page). |
| `RATCHET_FORWARDED_ALLOW_IPS` | string | `127.0.0.1` | Reverse proxies trusted for X-Forwarded-For (rate limits). |

### Notifications and ops

| Env var | Type | Default | Description |
| --- | --- | --- | --- |
| `RATCHET_NOTIFY_WEBHOOK` | secret (optional) | unset | Slack/Telegram webhook (the URL carries its own credential). |
| `RATCHET_DEBUG` | boolean | `false` | Content logging to a local file only. |
| `RATCHET_LOG_LEVEL` | string | `INFO` | Log level. |
| `RATCHET_MAX_EDITS_IN_FLIGHT` | integer | `2` | Concurrent edits. |

<!-- END GENERATED -->
