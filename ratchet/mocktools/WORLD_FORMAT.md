# World fixture format (mock tool server, record mode)

A *world* is the fictional, deterministic environment the mock tool server's adapters
serve in **record** mode (`ratchet.mocktools.world.World.load(path)`). Everything in it is
invented: the demo org is `acme`, people live at `example.com`, phone numbers use the
`555-01xx` range. Never put real data in a world. A minimal example lives in
`tests/fixtures/world/sample/`.

Each run gets its own deep copy of the world state, so writes (labels added, issues
closed, rows appended, mail sent) are visible to later calls *of the same run* only.

## Directory layout

Every file is optional; a missing file is an empty service. Unknown top-level keys in a
file are rejected at load (`ConfigError world_invalid` naming the file); items (issues,
messages, events, …) accept extra fields, which flow through to tool results.

```
<world>/
  world.json        manifest
  github.json       repos, issues, pull requests        -> github.*
  mailbox.json      inbox + outbox                      -> gmail.*
  slack.json        channels                            -> slack.post
  receipts/<name>.json  one structured receipt per file -> receipts.parse
  sheets.json       spreadsheets                        -> sheets.read / sheets.append
  calendar.json     events + extra busy blocks          -> calendar.*
  fx.json           exchange rates                      -> finance.rate
  payments.json     invoices + payments                 -> payments.pay
  web.json          pages + canned search results       -> web.search / web.fetch
  git.json          commit logs                         -> git.log
```

Times are ISO 8601 strings **with an offset** (`2026-10-14T09:00:00Z`); dates are
`YYYY-MM-DD`; money is a JSON number (strings for invoice amounts, as typed by agents).

### `world.json`
```json
{"name": "demo", "description": "…", "frozen_time": "2026-10-14T09:00:00Z"}
```
`frozen_time` is the fallback clock for `time.now` and for timestamps written by write
tools (see *Clock* below).

### `github.json`
```json
{"repos": {"acme/widgets": {
  "description": "…", "labels": ["bug", "stale"],
  "issues": [{"number": 12, "title": "…", "body": "…", "state": "open",
              "state_reason": null, "labels": ["bug"], "author": "dana", "assignees": [],
              "created_at": "…", "updated_at": "…", "closed_at": null,
              "comments": [{"comment_id": 1001, "author": "dana", "body": "…", "created_at": "…"}]}],
  "pulls": [{"number": 15, "title": "…", "body": "…", "head": "fix/x", "base": "main",
             "state": "open", "author": "dana", "created_at": "…", "merged_at": null}]}}}
```
`list_issues` returns issues sorted by number with `comments` replaced by a count;
`state` defaults to `open`; `labels` filters to issues having all of them. New comment ids
are `max + 1` (starting at 1001); new PR numbers are `max(issue, pr numbers) + 1`.

### `mailbox.json`
```json
{"address": "agent@example.com",
 "messages": [{"message_id": "msg_0001", "from": "alice@example.com", "to": ["…"],
               "cc": [], "subject": "…", "body": "…", "date": "…", "labels": ["INBOX"],
               "thread_id": null}],
 "outbox": []}
```
`gmail.search` query: whitespace-separated terms; `from:`, `to:`, `subject:`, `label:`
match that field (substring, case-insensitive); other words must appear in subject or
body. `gmail.send` appends `msg_out_0001…` to the outbox.

### `slack.json`
`{"channels": {"#ops": [{"author": "dana", "text": "…", "posted_at": "…"}]}}`

### `receipts/<name>.json`
```json
{"merchant": "City Taxi Berlin", "date": "2026-10-01", "currency": "EUR",
 "lines": [{"description": "Airport transfer", "quantity": 1, "unit_price": 42.02, "amount": 42.02}],
 "subtotal": 42.02, "vat": 7.98, "vat_rate": 0.19, "total": 50.00}
```
`receipts.parse {"file": "taxi-2026-10-01.pdf"}` looks up `receipts/taxi-2026-10-01.pdf.json`,
then `receipts/taxi-2026-10-01.json`. Only the base name is used; names are
`[A-Za-z0-9._ -]`, max 128 chars.

### `sheets.json`
`{"sheets": {"Expenses": {"header": ["date", "merchant", …], "rows": [["2026-09-28", …]]}}}`
Cells are strings (appended numbers are stringified). Appending to a missing sheet creates it.

### `calendar.json`
```json
{"events": [{"event_id": "evt_0001", "title": "Standup", "start": "…", "end": "…",
             "attendees": ["alice@example.com"], "calendar": "primary"}],
 "busy": {"bob@example.com": [{"start": "…", "end": "…"}]}}
```
`calendar.freebusy` returns, per attendee, every event (where they attend) and busy block
overlapping the window, sorted by start. `calendar.create` appends `evt_0002…`.

### `fx.json`
`{"rates": {"EUR/USD": {"2026-10-13": 1.0812, "2026-10-14": 1.0842}}}`
`finance.rate` uses the requested date or the latest earlier date; it falls back to the
inverse pair (`1/rate`, 6 decimals); `date` defaults to the call clock's date.

### `payments.json`
`{"invoices": {"INV-1001": {"amount": "250.00", "currency": "EUR", "payee": "…", "status": "open"}}, "payments": []}`
With invoices defined, unknown invoices are 404 and paid ones 409 `already_paid`. With
none defined, any invoice is accepted. Payment refs are `pay_0001…`.

### `web.json`
```json
{"pages": {"https://news.example.com/fx/eur-usd": {"title": "…", "text": "…", "published": "2026-10-14"}},
 "search": {"eur usd rate today": [{"title": "…", "url": "…", "snippet": "…"}]}}
```
`web.search` first looks for a canned query (case/whitespace-insensitive), else returns
every page whose title+text contains all query words, sorted by URL. `web.fetch` matches
the URL exactly, with or without a trailing slash.

### `git.json`
`{"repos": {"widgets": [{"sha": "c3d4e5f", "date": "…", "author": "…", "message": "…", "tags": ["v1.4.0"]}]}}`
Commits newest first. `since` is a tag (commits after it) or an ISO date (commits after
it). `acme/widgets` and `./widgets` both resolve to `widgets`.

## Tool errors

Adapters raise `not_found` (404), `bad_args` (400) or a conflict (409, e.g.
`pr_not_open`, `already_paid`). The error is returned as
`{"error": {"code", "message", "request_id"}}` with that status **and recorded in the
cassette**, so the replay answers the same way.

## Clock

`time.now` and write timestamps use, in order: the `X-Ratchet-Frozen-Time` header, the
run's `traces/<run_id>/run.json` `frozen_time`, `create_app(frozen_time=…)`, the world's
`world.json` `frozen_time`, then `ratchet.clock.now()`. In replay a `time.now` miss is
answered from the same clock.

## Cassettes

`data/cassettes/<tool>/<key>.json` (`ratchet.models.Cassette`), key =
`sha256(tool + "\n" + canonical_json(args_norm))`. `response` is `{"result": …}` for a
success or `{"error": {"code", "message"}, "status": 404}` for a recorded error; any
other mapping is served as the result itself (hand-authored cassettes). Args and
responses are PII-tokenized (values *and* dict keys) before they are written.

## World overlay (`world_ref`): "the world moved"

Bench D1 and demo scenario 3 change a volatile fact without changing the skill. That is
an **overlay cassette root**: a directory with the same `<tool>/<key>.json` layout holding
replacement cassettes (e.g. a `finance.rate` cassette with the new rate, same key as the
golden's). Pass it as `create_app(..., overlay_root=<dir>)`; the worker resolves
`ReplayBatch.world_ref` to that directory. For **world-dependent** tools only (catalog
`world_dependent=True`: `finance.rate`, `web.search`, `web.fetch`, `time.now`), lookup
order is: exact key in the overlay, exact key in the main cassettes, tolerant match in the
overlay, tolerant match in the main cassettes. All other tools ignore the overlay.

## Serving it to Hermes over MCP

The server exposes the non-local catalog tools at `/mcp` (streamable HTTP, stateless,
JSON responses) with `.` replaced by `_` (`github_close_issue`; Hermes shows it as
`mcp_ratchet_mock_github_close_issue`). The run id is a request header, so the Hermes
adapter writes it into the per-run `$HERMES_HOME/config.yaml`:

```yaml
mcp_servers:
  ratchet_mock:
    url: "http://127.0.0.1:8082/mcp"
    headers:
      X-Ratchet-Run-Id: "<run_id>"
      X-Ratchet-Frozen-Time: "2026-10-14T09:00:00Z"   # optional
    timeout: 120
```

A call without `X-Ratchet-Run-Id` is a tool error (`missing_run_id`). Blocked calls come
back as MCP tool errors whose text contains `ratchet_blocked`. DNS-rebinding protection
allows `127.0.0.1`, `localhost` and `[::1]` hosts; add others with
`create_app(mcp_allowed_hosts=[…])` (e.g. a Docker service name `mock-tools:*`).
