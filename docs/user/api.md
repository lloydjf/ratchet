# Dashboard API

The dashboard is a client of a small JSON API served by `ratchet serve` on port 8080. Use the
API to script anything the dashboard does: start a Simulate run, follow the event stream,
read a verdict, roll back or approve.

- **Base path:** `/api/v1`. `/api/...` is an alias for compatibility (ADR 0019). The
  dashboard pins `/api/v1`.
- **Format:** JSON everywhere. Times are ISO 8601 in UTC. Money is a plain number of US
  dollars, never a string.
- **Pagination:** `?cursor=&limit=` (max 200). The response is
  `{"items": [...], "next_cursor": "..." | null}`.
- **CORS:** same-origin only. The API serves the dashboard itself.
- **OpenAPI:** `uv run ratchet api export-openapi > schemas/openapi.json` writes the full
  document. CI checks that it matches the code, and the dashboard's TypeScript types are
  generated from it.
- **Request ids:** send `X-Request-Id` and it's echoed back and logged. Without one, Ratchet
  generates an id.

## Authentication and the public host

The API has no user accounts. A local install trusts whoever can reach port 8080, and binds
to `127.0.0.1` by default (`RATCHET_DASHBOARD_HOST`). On a host with
`RATCHET_PUBLIC_MODE=true`:

- `GET /api/v1/meta` (or `POST /simulate`) issues a `ratchet_session` cookie: HttpOnly,
  SameSite=Lax, Secure on https, valid for 24 hours. Ratchet stores only its hash,
  `sha256(cookie)[:16]`.
- Every `POST`/`PUT` action needs the header `X-Ratchet-Action-Token`. Its current value is
  `action_token` in `GET /api/v1/meta`, and it changes every UTC day. A missing or stale
  token is `403 action_token_invalid`.
- Actions are rate-limited to 10 a minute per client IP (`429 rate_limited`, with
  `Retry-After`).
- Bless, unbless, criteria edits, the golden library and the settings view answer
  `403 not_public`. So do verdicts from another visitor's Simulate session.

```bash
# start scenario 1 on a public host
TOKEN=$(curl -s -c jar https://ratchet.example.com/api/v1/meta | jq -r .action_token)
curl -s -b jar -X POST -H "X-Ratchet-Action-Token: $TOKEN" \
     https://ratchet.example.com/api/v1/simulate/1
```

## Event stream (SSE)

`GET /api/v1/events/stream` is a Server-Sent Events stream of rows from the events table,
the same rows the timeline and the progress strip read.

```text
id: 4182
event: verdict.decided
data: {"id": 4182, "ts": "2026-10-16T09:12:44Z", "kind": "verdict.decided", "ref": "verdict_9f…", "payload": {...}, "session_hash": "1c0e…"}

: ping
```

- Each frame has `id:` (the event id), `event:` (the kind) and `data:` (the event row as JSON:
  `id`, `ts`, `kind`, `ref`, `payload`, `session_hash`).
- A heartbeat comment arrives every 15 seconds.
- To resume, reconnect with `Last-Event-ID` (browsers do this for you) or `?since=<id>`. The
  stream continues after the larger of the two. With neither, it streams only new events.
- `?kinds=verdict.decided,edit.detected` filters by kind.
- The stream carries global events plus the events of your own session cookie.
- Caps: `RATCHET_SSE_MAX_PER_CLIENT` streams per client IP (3) and `RATCHET_SSE_MAX_STREAMS` in
  total (100). Over that, the server answers `429 rate_limited`.

For polling, `GET /api/v1/events?since&kinds&limit&cursor` returns the same rows as a page.

### Event kinds

| Kind | Emitted when | Shown in the dashboard as |
| --- | --- | --- |
| `trace.blessed` | a run becomes a golden | timeline "Blessed: …" |
| `edit.detected` | the watcher commits a skill change to `candidate` | timeline item, stage 1 |
| `edit.selected` | goldens are chosen for replay | stage "Choosing tasks" |
| `batch.submitted`, `batch.heartbeat`, `batch.finished` | replay batches run | stage "Replaying on …" (k/n) |
| `pair.level0` | Level 0 compares side effects | stage "Checking actions" |
| `pair.opinion` | a judge tier answers | stages Nano / Super / Ultra, opinion cards |
| `pair.drift` | Tavily checks a fact | drift cards |
| `pair.decided` | a pair gets its outcome | pair grid |
| `verdict.decided` | the decision is made | verdict header, timeline |
| `verdict.acted` | merge, hold or revert ran | timeline, toast |
| `verdict.rolled_back`, `pair.reblessed` | a person acted | timeline |
| `simulate.started`, `simulate.finished` | a Simulate run starts or ends | scenario card |
| `digest.written` | the weekly digest is written | digest page |
| `orchestrator.state` | the edit moves to a new pipeline state | stage timeline on the verdict page |

## Errors

Every error has the same envelope:

```json
{
  "error": {
    "code": "already_reverted",
    "message": "This edit is already reverted; main is unchanged.",
    "request_id": "req_5c1d…",
    "details": null
  }
}
```

`code` is a stable string. The same word appears in HTTP responses, pair evidence, verdict
reasons and logs, so a script, a test and a person all see the same thing. Match on `code`,
never on `message`. The full table is under [Error codes](#error-codes).

## Actions and their conflicts

| Request | Answer |
| --- | --- |
| Rollback or Approve before the edit's pipeline finished | `409 not_ready` |
| Rollback on a verdict whose action is `reverted` (a BLOCK, or already rolled back) | `409 already_reverted` |
| Rollback of a merged edit that `main` cannot take | `409 stacked_on_held` |
| Approve on a `merged` verdict | `200`, unchanged (idempotent) |
| Approve on a `reverted` verdict | "approve anyway": the revert is reverted on `candidate` and merged under the stacking guard; the verdict becomes `merged` |
| Approve while an earlier held edit sits between `main` and this one | `409 stacked_on_held` (ADR 0021) |
| Two actions on one verdict at once | `409 action_locked` |

<!-- BEGIN GENERATED by scripts/gen_api_docs.py -->

!!! info "Generated section"
    Generated by `scripts/gen_api_docs.py`. Do not edit it by hand: change the source,
    then run `uv run python scripts/gen_docs.py`.

## Endpoints

30 operations, from `schemas/openapi.json` (API version `0.1.0`).

| Method | Path | Summary | Response |
| --- | --- | --- | --- |
| `GET` | [`/api/v1/costs`](#get-api-v1-costs) | Get Costs | [`CostsView`](reference/api-models.md#costsview) |
| `POST` | [`/api/v1/demo/events`](#post-api-v1-demo-events) | Post Demo Event | no body |
| `GET` | [`/api/v1/digest/latest`](#get-api-v1-digest-latest) | Latest Digest | [`DigestView`](reference/api-models.md#digestview) |
| `GET` | [`/api/v1/edits`](#get-api-v1-edits) | List Edits | [`Page_EditView_`](reference/api-models.md#page_editview_) |
| `GET` | [`/api/v1/edits/{edit_id}`](#get-api-v1-edits-edit-id) | Get Edit | [`EditView`](reference/api-models.md#editview) |
| `GET` | [`/api/v1/edits/{edit_id}/logs`](#get-api-v1-edits-edit-id-logs) | Get Edit Logs | [`LogsView`](reference/api-models.md#logsview) |
| `POST` | [`/api/v1/edits/{edit_id}/replay`](#post-api-v1-edits-edit-id-replay) | Replay Edit | [`EditView`](reference/api-models.md#editview) |
| `GET` | [`/api/v1/events`](#get-api-v1-events) | List Events | [`Page_Event_`](reference/api-models.md#page_event_) |
| `GET` | [`/api/v1/events/stream`](#get-api-v1-events-stream) | Stream Events | SSE stream (`text/event-stream`) |
| `GET` | [`/api/v1/meta`](#get-api-v1-meta) | Get Meta | [`Meta`](reference/api-models.md#meta) |
| `GET` | [`/api/v1/metrics/demo`](#get-api-v1-metrics-demo) | Demo | [`MetricsView`](reference/api-models.md#metricsview) |
| `GET` | [`/api/v1/metrics/product`](#get-api-v1-metrics-product) | Product | [`MetricsView`](reference/api-models.md#metricsview) |
| `POST` | [`/api/v1/pairs/{golden}/{replay}/rebless`](#post-api-v1-pairs-golden-replay-rebless) | Rebless | [`ReblessView`](reference/api-models.md#reblessview) |
| `GET` | [`/api/v1/runs`](#get-api-v1-runs) | List Runs | [`RunsView`](reference/api-models.md#runsview) |
| `POST` | [`/api/v1/runs/{run_id}/bless`](#post-api-v1-runs-run-id-bless) | Bless | [`Trace`](reference/api-models.md#trace) |
| `GET` | [`/api/v1/scenarios`](#get-api-v1-scenarios) | List Scenarios | [`ScenarioView[]`](reference/api-models.md#scenarioview) |
| `GET` | [`/api/v1/settings`](#get-api-v1-settings) | Get Settings View | [`SettingsView`](reference/api-models.md#settingsview) |
| `POST` | [`/api/v1/simulate/{scenario_id}`](#post-api-v1-simulate-scenario-id) | Start Simulate | [`SimulateView`](reference/api-models.md#simulateview) |
| `GET` | [`/api/v1/ticker`](#get-api-v1-ticker) | Get Ticker | [`TickerView`](reference/api-models.md#tickerview) |
| `GET` | [`/api/v1/traces`](#get-api-v1-traces) | List Traces | [`Page_TraceSummary_`](reference/api-models.md#page_tracesummary_) |
| `GET` | [`/api/v1/traces/{run_id}`](#get-api-v1-traces-run-id) | Get Trace | [`Trace`](reference/api-models.md#trace) |
| `PUT` | [`/api/v1/traces/{run_id}/criteria`](#put-api-v1-traces-run-id-criteria) | Put Criteria | [`Trace`](reference/api-models.md#trace) |
| `POST` | [`/api/v1/traces/{run_id}/unbless`](#post-api-v1-traces-run-id-unbless) | Unbless | [`Trace`](reference/api-models.md#trace) |
| `GET` | [`/api/v1/verdicts`](#get-api-v1-verdicts) | List Verdicts | [`Page_VerdictSummary_`](reference/api-models.md#page_verdictsummary_) |
| `GET` | [`/api/v1/verdicts/{verdict_id}`](#get-api-v1-verdicts-verdict-id) | Get Verdict | [`VerdictView`](reference/api-models.md#verdictview) |
| `POST` | [`/api/v1/verdicts/{verdict_id}/approve`](#post-api-v1-verdicts-verdict-id-approve) | Approve | [`VerdictView`](reference/api-models.md#verdictview) |
| `GET` | [`/api/v1/verdicts/{verdict_id}/pairs/{golden}/{replay}`](#get-api-v1-verdicts-verdict-id-pairs-golden-replay) | Get Pair | [`PairView`](reference/api-models.md#pairview) |
| `POST` | [`/api/v1/verdicts/{verdict_id}/rollback`](#post-api-v1-verdicts-verdict-id-rollback) | Rollback | [`VerdictView`](reference/api-models.md#verdictview) |
| `GET` | [`/healthz`](#get-healthz) | Healthz | [`HealthView`](reference/api-models.md#healthview) |
| `GET` | [`/metrics`](#get-metrics) | Metrics | text (`text/plain`) |

## Meta and settings

### GET `/api/v1/meta` {#get-api-v1-meta}

Host configuration for the SPA. Issues the `ratchet_session` cookie on first call.

Response `200`: [`Meta`](reference/api-models.md#meta); errors: `4XX`, `5XX`.

### GET `/api/v1/settings` {#get-api-v1-settings}

Read-only caps, runner and models. Hidden on the public host (403 `not_public`).

Response `200`: [`SettingsView`](reference/api-models.md#settingsview); errors: `4XX`, `5XX`.

## Simulate

### GET `/api/v1/scenarios` {#get-api-v1-scenarios}

The seeded scenarios; empty when Simulate is disabled or not wired.

Response `200`: [`ScenarioView[]`](reference/api-models.md#scenarioview); errors: `4XX`, `5XX`.

### POST `/api/v1/simulate/{scenario_id}` {#post-api-v1-simulate-scenario-id}

Apply the scenario's seeded edit in this session's copy of the skills repo. The response carries the pre-allocated `verdict_id` so the client can open it at once.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `scenario_id` | path | string | yes | — |

Response `200`: [`SimulateView`](reference/api-models.md#simulateview); errors: `4XX`, `5XX`.

## Verdicts and actions

### POST `/api/v1/pairs/{golden}/{replay}/rebless` {#post-api-v1-pairs-golden-replay-rebless}

The replay becomes the golden; the old golden is archived (reason `drift`). On the public host this writes a session-scoped golden copy and never touches the library.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `golden` | path | string | yes | — |
| `replay` | path | string | yes | — |

Response `200`: [`ReblessView`](reference/api-models.md#reblessview); errors: `4XX`, `5XX`.

### GET `/api/v1/verdicts` {#get-api-v1-verdicts}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `cursor` | query | string or null | no | — |
| `limit` | query | integer (min 1, max 200) | no | `50` |

Response `200`: [`Page_VerdictSummary_`](reference/api-models.md#page_verdictsummary_); errors: `4XX`, `5XX`.

### GET `/api/v1/verdicts/{verdict_id}` {#get-api-v1-verdicts-verdict-id}

While the edit runs: `verdict=null` and `progress` from the orchestrator.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `verdict_id` | path | string | yes | — |

Response `200`: [`VerdictView`](reference/api-models.md#verdictview); errors: `4XX`, `5XX`.

### POST `/api/v1/verdicts/{verdict_id}/approve` {#post-api-v1-verdicts-verdict-id-approve}

Merge candidate into `main`.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `verdict_id` | path | string | yes | — |

Response `200`: [`VerdictView`](reference/api-models.md#verdictview); errors: `4XX`, `5XX`.

### GET `/api/v1/verdicts/{verdict_id}/pairs/{golden}/{replay}` {#get-api-v1-verdicts-verdict-id-pairs-golden-replay}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `verdict_id` | path | string | yes | — |
| `golden` | path | string | yes | — |
| `replay` | path | string | yes | — |

Response `200`: [`PairView`](reference/api-models.md#pairview); errors: `4XX`, `5XX`.

### POST `/api/v1/verdicts/{verdict_id}/rollback` {#post-api-v1-verdicts-verdict-id-rollback}

Revert the edit's commit on candidate; `main` is unchanged (409 `action_locked` when another action on this verdict is in progress).

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `verdict_id` | path | string | yes | — |

Response `200`: [`VerdictView`](reference/api-models.md#verdictview); errors: `4XX`, `5XX`.

## Edits

### GET `/api/v1/edits` {#get-api-v1-edits}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `cursor` | query | string or null | no | — |
| `limit` | query | integer (min 1, max 200) | no | `50` |

Response `200`: [`Page_EditView_`](reference/api-models.md#page_editview_); errors: `4XX`, `5XX`.

### GET `/api/v1/edits/{edit_id}` {#get-api-v1-edits-edit-id}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `edit_id` | path | string | yes | — |

Response `200`: [`EditView`](reference/api-models.md#editview); errors: `4XX`, `5XX`.

### GET `/api/v1/edits/{edit_id}/logs` {#get-api-v1-edits-edit-id-logs}

The debug drawer's real logs. Operator host only (403 `not_public`).

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `edit_id` | path | string | yes | — |

Response `200`: [`LogsView`](reference/api-models.md#logsview); errors: `4XX`, `5XX`.

### POST `/api/v1/edits/{edit_id}/replay` {#post-api-v1-edits-edit-id-replay}

Force a replay: the orchestrator resets the edit to `selecting`. Operator hosts only: on the public host a replay would re-run the whole pipeline outside Simulate's one-run-per-visitor gate and spend the shared daily cap.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `edit_id` | path | string | yes | — |

Response `200`: [`EditView`](reference/api-models.md#editview); errors: `4XX`, `5XX`.

## Traces and blessing

### GET `/api/v1/runs` {#get-api-v1-runs}

Recent runs, newest first; `unblessed=1` (default) leaves out the goldens.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `unblessed` | query | boolean | no | `True` |
| `limit` | query | integer (min 1, max 200) | no | `50` |

Response `200`: [`RunsView`](reference/api-models.md#runsview); errors: `4XX`, `5XX`.

### POST `/api/v1/runs/{run_id}/bless` {#post-api-v1-runs-run-id-bless}

Mark a live run golden (PII pass, Nano enrichment). Refused on the public host.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `run_id` | path | string | yes | — |

Response `200`: [`Trace`](reference/api-models.md#trace); errors: `4XX`, `5XX`.

### GET `/api/v1/traces` {#get-api-v1-traces}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `kind` | query | `golden` \| `live` \| `replay` | no | `golden` |
| `skill` | query | string or null (max len 64) | no | — |
| `search` | query | string or null (max len 200) | no | — |
| `cursor` | query | string or null | no | — |
| `limit` | query | integer (min 1, max 200) | no | `50` |

Response `200`: [`Page_TraceSummary_`](reference/api-models.md#page_tracesummary_); errors: `4XX`, `5XX`.

### GET `/api/v1/traces/{run_id}` {#get-api-v1-traces-run-id}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `run_id` | path | string | yes | — |

Response `200`: [`Trace`](reference/api-models.md#trace); errors: `4XX`, `5XX`.

### PUT `/api/v1/traces/{run_id}/criteria` {#put-api-v1-traces-run-id-criteria}

Replace the golden's success criteria.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `run_id` | path | string | yes | — |

Request body: [`CriteriaIn`](reference/api-models.md#criteriain)

Response `200`: [`Trace`](reference/api-models.md#trace); errors: `4XX`, `5XX`.

### POST `/api/v1/traces/{run_id}/unbless` {#post-api-v1-traces-run-id-unbless}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `run_id` | path | string | yes | — |

Response `200`: [`Trace`](reference/api-models.md#trace); errors: `4XX`, `5XX`.

## Events

### GET `/api/v1/events` {#get-api-v1-events}

Newest first; with `since`, oldest first after that id (the polling fallback).

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `since` | query | integer or null (min 0) | no | — |
| `kinds` | query | string or null (max len 1000) | no | — |
| `limit` | query | integer (min 1, max 200) | no | `50` |
| `cursor` | query | string or null | no | — |

Response `200`: [`Page_Event_`](reference/api-models.md#page_event_); errors: `4XX`, `5XX`.

### GET `/api/v1/events/stream` {#get-api-v1-events-stream}

`id:`/`event:`/`data:` frames; heartbeat comment every 15 s; resumes after `Last-Event-ID` (or `since`), else streams only new events. At most `ratchet_sse_max_per_client` streams per client and `ratchet_sse_max_streams` in total; over that, 429 `rate_limited`.

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `since` | query | integer or null (min 0) | no | — |
| `kinds` | query | string or null (max len 1000) | no | — |
| `last-event-id` | header | string or null (max len 32) | no | — |

Response `200`: SSE stream (`text/event-stream`); errors: `4XX`, `5XX`.

## Costs

### GET `/api/v1/costs` {#get-api-v1-costs}

Spend tiles; with `from`/`to` (ISO 8601) also the totals, purposes and days of that window (`window`, `by_purpose`, `by_day`).

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `from` | query | string (date-time) or null | no | — |
| `to` | query | string (date-time) or null | no | — |

Response `200`: [`CostsView`](reference/api-models.md#costsview); errors: `4XX`, `5XX`.

### GET `/api/v1/ticker` {#get-api-v1-ticker}

Today's spend by tier; `live` is true while an edit this caller can see is running.

Response `200`: [`TickerView`](reference/api-models.md#tickerview); errors: `4XX`, `5XX`.

## Digest

### GET `/api/v1/digest/latest` {#get-api-v1-digest-latest}

Response `200`: [`DigestView`](reference/api-models.md#digestview); errors: `4XX`, `5XX`.

## Demo analytics

### POST `/api/v1/demo/events` {#post-api-v1-demo-events}

Request body: [`DemoEventIn`](reference/api-models.md#demoeventin)

Response `204`: no body; errors: `4XX`, `5XX`.

## Operations

### GET `/healthz` {#get-healthz}

`ok` follows the database; extra probes (e.g. the proxy, wired by `ratchet serve`) are reported but only fail the check if they are down.

Response `200`: [`HealthView`](reference/api-models.md#healthview); errors: `503`.

### GET `/metrics` {#get-metrics}

Response `200`: text (`text/plain`).

## Metrics

### GET `/api/v1/metrics/demo` {#get-api-v1-metrics-demo}

The anonymous demo funnel. Operator host only (403 `not_public`).

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `since` | query | string | no | `7d` |

Response `200`: [`MetricsView`](reference/api-models.md#metricsview); errors: `4XX`, `5XX`.

### GET `/api/v1/metrics/product` {#get-api-v1-metrics-product}

| Parameter | In | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `since` | query | string | no | `7d` |

Response `200`: [`MetricsView`](reference/api-models.md#metricsview); errors: `4XX`, `5XX`.

## Error codes

Every stable code, from the registry in `docs/CONTRACTS.md` §8. The user-facing text
is what the dashboard and CLI show: **long** from `ERROR_COPY` (dialogs, banners,
toasts), **short** from `REASON_TEXT` (pair evidence, badges). A dash means the code
is internal or operator-only.

| Code | HTTP | Raised by | User-facing text |
| --- | --- | --- | --- |
| `bad_request` | 400 / 422 | API validation, bad cursor/filter/`from`-`to` | — |
| `not_found` | 404 | unknown id or route | — |
| `method_not_allowed` | 405 | API | — |
| `payload_too_large` | 413 | API body limit (`RATCHET_API_MAX_BODY_BYTES`) | **long:** That request is too large. Ratchet only accepts small JSON bodies.<br>**short:** Request too large. |
| `not_public` | 403 | public host: bless/record/library, another session's verdict | **long:** Blessing and recording are off on the demo. Run Ratchet locally to protect your own agent.<br>**short:** Not available on the demo. |
| `simulate_disabled` | 403 | Simulate off on this host | **long:** Simulate is off on this host. Recorded runs are on the timeline.<br>**short:** Simulate is off on this host. |
| `simulate_busy` | 429 | Simulate queue full or client already has a run | **long:** Many judges are running scenarios right now. Try again in a minute, or view a recorded run.<br>**short:** Simulate is busy; try again shortly. |
| `rate_limited` | 429 | 10 actions/min/IP; SSE stream caps | **long:** Too many actions in a minute. Wait a moment and try again.<br>**short:** Too many actions; wait a moment. |
| `action_token_invalid` | 403 | public host: missing/stale `X-Ratchet-Action-Token` | **long:** This page is out of date. Reload and try again.<br>**short:** Page out of date; reload. |
| `action_locked` | 409 | another action holds the verdict lock | **short:** Someone else is acting on this verdict. |
| `not_ready` | 409 | Rollback/Approve before the edit is terminal (or before its revert ran) | **long:** Ratchet is still acting on this verdict. Try again in a few seconds.<br>**short:** Still acting on this verdict. |
| `not_ready` | 503 | API: a component is not wired (no orchestrator / module) | **long:** Ratchet is still acting on this verdict. Try again in a few seconds.<br>**short:** Still acting on this verdict. |
| `already_reverted` | 409 | Rollback on a verdict whose action is `reverted` | **long:** This edit is already reverted; main is unchanged.<br>**short:** Already reverted; main is unchanged. |
| `stacked_on_held` | 409 / reason | merge or approve-anyway would ship an unresolved earlier edit; rollback of a merged edit main cannot take | **long:** An earlier edit on candidate is still held. Approve or roll it back first; merging this one would ship it too.<br>**short:** Held: an earlier held edit must be resolved first. |
| `merge_conflict` | 409 / reason | main moved under the edit | **long:** The skill files conflict with main. Resolve the conflict in the skills repo, then approve.<br>**short:** Held: skill files conflict with main. |
| `session_reset` | 409 | Simulate session repo already reset | — |
| `not_seeded` | 409 | `ratchet seed-demo` not run | — |
| `conflict` | 409 | generic `ConflictError` | — |
| `tier_mismatch` | 409 | proxy: agent call on a tier other than the run's | **short:** Replay refused: model tier differs from the original run. |
| `budget_exceeded` | 429 | proxy spending caps (daily, purpose, bench, sidecar allowance) | **long:** Spending cap reached for today. The edit is held; raise RATCHET_DAILY_USD_CAP in .env or wait until tomorrow.<br>**short:** Paused: spending cap reached. |
| `upstream_unavailable` | 502 | Token Factory / Tavily / Nebius API failed | **long:** The model service didn't respond. Ratchet judged this pair as not sure and will keep the edit held.<br>**short:** Model unavailable; judged as uncertain. |
| `sidecar_token_invalid` | 403 | worker sidecar admin route without the per-golden token | — |
| `run_id_mismatch` | 403 | worker mock-tool sidecar asked about another run | — |
| `missing_run_id`, `bad_run_id`, `bad_frozen_time` | 400 | mock tool server request checks | — |
| `unknown_tool`, `bad_args`, `not_local`, `local_tool` | 400 / 404 | mock tool server and harness tool routing | — |
| `world_missing`, `world_invalid` | 409 / 500 | mock world fixtures | — |
| `already_paid`, `pr_not_open` | 409 | mock tool adapters (world state) | — |
| `reconciled_by_diff` | — | workspace-diff `fs.write`/`fs.delete` ToolCalls | — |
| `ratchet_blocked` | 409 | unexpected side effect refused by the mock tool server | **short:** blocked action |
| `sandbox_failed` | 500 / pair | sandbox could not start or was refused (ADR 0017) | **short:** Sandbox error on this task. |
| `harness_failed`, `harness_timeout` | 500 / pair | agent crashed or timed out in replay | **short:** Agent crashed during replay. |
| `harness_mismatch` | pair | golden and replay ran on different adapters | **short:** Cannot replay: different agent runtime. |
| `worker_lost` | pair | worker died before the golden ran | **long:** The replay worker stopped before this task ran. The edit is held. Try again from the edit page.<br>**short:** Replay worker stopped; held for review. |
| `schema_failure` | 422 / pair | judge answer unreadable after retries → `uncertain` | **short:** Judge answer unreadable; treated as uncertain. |
| `judge_timeout` | pair / reason | Ultra timed out → held | **long:** The final judge timed out. The edit is held for you to review.<br>**short:** Final judge timed out; held for review. |
| `drift_unavailable` | pair | Tavily failed → stays `regression` | **long:** Couldn't check whether the world changed. Treating this difference as a possible regression.<br>**short:** Could not verify whether the world changed. |
| `internal_error` | 500 | unexpected exception (envelope) | — |
| `internal` | — | watcher internal failure (log) | — |
| `config_error` | 500 / CLI | bad settings; public mode with the default action-token secret | — |
| `git_error`, `git_timeout`, `git_unavailable`, `main_integrity` | 500 | git operations | — |
| `skill_invalid` | 422 | SKILL.md failed validation | — |
| `confirmation_required` | CLI | `ratchet bench` without `--yes` | — |

<!-- END GENERATED -->
