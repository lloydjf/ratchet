# Operations

This page condenses the operator runbook (`docs/RUNBOOK.md`) and the scripts in `deploy/`.
It covers running Ratchet as a long-lived service on a VM: the public demo host, or a
machine next to your own agent.

Every procedure is a script in `deploy/vm/` (run as root on the VM) or in `deploy/` (local
Docker). The scripts log to stderr with UTC timestamps, take a shared lock
(`/var/lib/ratchet-ops/ops.lock`), are idempotent, and post to `RATCHET_NOTIFY_WEBHOOK` when
it is set.

## Layout on the VM

| Path | What | Owner / mode |
| --- | --- | --- |
| `/opt/ratchet` | git checkout, `.venv` (Python 3.11 via uv), `dashboard/dist` | `ratchet`, read-only to the service |
| `/srv/ratchet/data`, `/srv/ratchet/skills` | traces, cassettes, SQLite (WAL), verdicts; the skills git repo | `ratchet` 0750, the service's only writable paths |
| `/srv/ratchet/pii.key` | Fernet key for the PII token map, kept out of `data/` and out of backups | `ratchet` 0600 |
| `/srv/ratchet/tmp` | `TMPDIR` for replays (visible to the Docker daemon) | `ratchet` |
| `/etc/ratchet/env` | every setting and secret (systemd `EnvironmentFile`) | `root` 0600 |
| `/var/lib/ratchet-ops/deploys.tsv` | the deploy ledger that rollback reads | `root` |
| `/var/backups/ratchet` | nightly tarballs, kept 14 days | `root` 0700 |
| `/var/www/ratchet-fallback` | static fallback page (demo host) | `root` |
| `/var/www/ratchet-site` | caddy edge only: the apex site (product page, `/docs/`, `/bench/`), a symlink to the live release | `root` |
| `/etc/ratchet/cloudflared-token` | tunnel edge only: the Cloudflare Tunnel token, handed to the connector as a systemd credential and never logged | `root` 0600 |
| `/etc/ratchet/nebius-sa.json` | Nebius service-account credentials, Serverless Jobs only | `root` 0600 |

**Units.** `ratchet-serve.service` runs one `ratchet serve` process: dashboard and API on
127.0.0.1:8080, proxy on 8081, mock tools on 8082, orchestrator and watcher. It has the
aliases `ratchet-proxy`, `ratchet-mocktools`, `ratchet-orchestrator` and `ratchet-dashboard`.
`caddy.service` either terminates TLS on ports 80 and 443 for every host name (caddy edge)
or serves the demo host as plain HTTP on `127.0.0.1:80` only (tunnel edge), where
`ratchet-tunnel.service` (`cloudflared`) connects it to Cloudflare. Four timers run
alongside: `ratchet-backup.timer` (03:00 UTC), `ratchet-token-rotate.timer` (Mondays 04:30
UTC), `ratchet-alerts.timer` (every 2 minutes) and `ratchet-nebius-token.timer` (every 4 hours
and at boot; it does nothing without the Nebius credentials file).

## Hosting layout

Three public names:

| Name | Serves |
| --- | --- |
| `ratchetci.dev` | a static product page, the docs at `/docs/` and the benchmark report at `/bench/` |
| `www.ratchetci.dev` | a 301 redirect to the apex |
| `demo.ratchetci.dev` | the dashboard, API and live event stream in public mode (`RATCHET_PUBLIC_URL`), with a static fallback page when the app is down |

`RATCHET_EDGE` in `/etc/ratchet/env` (`bootstrap.sh --edge`) decides who faces the internet:

| | `tunnel`: Cloudflare in front | `caddy` (default): one public VM |
| --- | --- | --- |
| Apex, `/docs/`, `/bench/` | Cloudflare static assets (the Worker `ratchetci-site`), deployed by `.github/workflows/site.yml` on every push to `main` that touches them | files on the VM, rebuilt from the deployed commit by every deploy |
| `www` -> apex | a Cloudflare Redirect Rule | Caddy (also the names in `RATCHET_ALT_ADDRESSES`) |
| Demo host | a Cloudflare Tunnel to `cloudflared` on the VM, then Caddy on `127.0.0.1:80` | Caddy on ports 80 and 443 |
| Certificates | Cloudflare's | Caddy's, by ACME HTTP-01 |
| DNS | created by Cloudflare: the Worker's custom domains and the tunnel's CNAME, all proxied | A records for the apex and `demo` pointing at the VM, a CNAME from `www` to the apex, all **DNS only** |
| Inbound ports on the VM | SSH only (ufw on by default) | 22, 80, 443 |
| Standby VM takes over | start the connector there with the same token; no DNS change | move the A records |

The tunnel is configured remotely, in Cloudflare: one public hostname, `demo.ratchetci.dev`
-> `http://127.0.0.1:80`, and the catch-all `http_status:404`. The VM only runs the
connector: `deploy/vm/edge-tunnel.sh` installs `cloudflared` from Cloudflare's apt repository
(signing key fingerprint pinned), reads the token from `/etc/ratchet/cloudflared-token`
(written with `edge-tunnel.sh set-token`, never echoed), and has `status` and `uninstall`
subcommands. The live event stream works through Cloudflare unchanged: it is never
compressed or cached, and its 15-second heartbeat keeps it inside Cloudflare's idle limit.

`deploy/vm/dns.md` has the records for both layouts, the tunnel and Redirect Rule steps,
and Cloudflare API examples; the header of `.github/workflows/site.yml` has the one-time
setup of the static site (API token, repository secrets `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID`).

The apex is static files either way, so it stays up while the app restarts or fails over.
Its content security policy allows no inline scripts except the docs theme's own, by hash:
Caddy gets the hashes from each deploy, the static site from its `_headers` file, which
each build writes (one rule for the whole site, so the two never combine into a stricter
policy). `'unsafe-inline'` is never allowed for scripts.

## Status at a glance

```bash
systemctl status ratchet-serve caddy --no-pager
systemctl list-timers 'ratchet-*' --no-pager
curl -fsS http://127.0.0.1:8080/healthz; echo
sudo deploy/vm/failover.sh status
journalctl -u ratchet-serve -n 100 --no-pager -o cat        # JSON lines, 7-day retention
journalctl -u ratchet-serve --since -1h -o cat | grep '"level":"ERROR"'
tail -n 20 /var/lib/ratchet-ops/deploys.tsv
```

Run the CLI as the service user, with its env file and sandboxing:

```bash
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && ratchet_cli doctor'
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && ratchet_cli logs --edit <edit_id>'
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && ratchet_cli metrics --since 1d --demo'
```

## VM bootstrap

On a fresh Ubuntu 24.04 Nebius CPU VM (2 vCPU / 8 GB). Tunnel edge: write the tunnel token
first. Caddy edge: point DNS at the VM first and leave out `--edge`.

```bash
git clone https://github.com/<org>/ratchet /tmp/ratchet
sudo /tmp/ratchet/deploy/vm/edge-tunnel.sh set-token < token.txt && shred -u token.txt   # tunnel
sudo /tmp/ratchet/deploy/vm/bootstrap.sh --edge tunnel --public-url https://demo.ratchetci.dev \
     --site-url https://ratchetci.dev --repo https://github.com/<org>/ratchet --ref main
sudoedit /etc/ratchet/env        # NEBIUS_TOKEN_FACTORY_KEY, TAVILY_API_KEY, RATCHET_UPSTREAM=live, …
sudo systemctl restart ratchet-serve
```

`bootstrap.sh` is idempotent: re-run it to converge a host. It never overwrites values in
`/etc/ratchet/env`; it only appends keys that are new in `deploy/vm/env.template`. Opt-ins:

- `--ufw on|off` (`RATCHET_ENABLE_UFW` in the env file) controls the firewall. It is on by
  default with the tunnel edge (22/tcp only) and off with the caddy edge (on: 22, 80, 443).
  As an environment variable it only works inside `sudo`, because sudo drops variables set
  before it.
- `RATCHET_SKIP_SEED=1` skips seeding the demo.
- `RATCHET_SKIP_WORKER_IMAGE=1` skips building the worker image.

`--public-url` is the demo host; `--site-url` is the apex; `--edge` is `tunnel` or `caddy`.
Switch an existing host with `deploy.sh <sha> --edge tunnel|caddy`. For Serverless Jobs, copy the
service-account credentials to `/etc/ratchet/nebius-sa.json` (root, mode 0600) before
bootstrap, or afterwards followed by `sudo systemctl start ratchet-nebius-token`; see
[Rotate secrets](#rotate-secrets).

Then set up an external uptime monitor (`deploy/vm/uptime.md`):
`GET https://demo.ratchetci.dev/healthz` every minute, alerting after 2 consecutive failures,
a check on `https://ratchetci.dev/`, and a daily TLS-expiry check for both names (with the
tunnel edge these are Cloudflare's certificates).

!!! warning "Public host checklist"
    `RATCHET_PUBLIC_MODE=true`, a long random `RATCHET_ACTION_TOKEN_SECRET` (`ratchet serve`
    refuses the default), `RATCHET_SANDBOX=docker` or `openshell` (the `process` sandbox is
    refused), `RATCHET_FORWARDED_ALLOW_IPS` set to Caddy, and `RATCHET_DEBUG` off. See
    [Public mode](configuration.md#public-mode).

## Deploy

```bash
# wait for CI to go green on <sha>; on main, CI also pushes ghcr.io/<org>/ratchet-worker:<sha>
sudo /opt/ratchet/deploy/vm/deploy.sh <sha>
```

The script runs these steps in order:

1. Checks the GitHub check runs for `<sha>`. `--skip-ci-check` bypasses this, and
   `RATCHET_REQUIRE_CI=1` makes an unreachable API fatal.
2. Checks out the commit, then runs `uv sync --frozen --no-dev` and `npm ci && npm run build`.
3. Runs `ratchet db migrate`. Migrations are forward-only and idempotent.
4. Restarts `ratchet-serve` and waits for `/healthz`.
5. Runs the smoke test: `ratchet doctor`, then `ratchet simulate 1 --headless`, which must
   BLOCK within 90 s.
6. Appends the result to the ledger and to `docs/DEPLOYS.md`.

With the caddy edge the deploy also rebuilds the apex site from the same commit. With the
tunnel edge the apex is not on the VM: the Site workflow builds it (docs generation,
`mkdocs build --strict`, `render_site.py --cloudflare`) and publishes it with
`wrangler deploy` whenever `main` changes the docs, the benchmark results or the page.

With `RATCHET_WORKER_IMAGE_REPO=ghcr.io/<org>/ratchet-worker` in the env file, the script
pulls and pins the CI-built worker for `<sha>`. Otherwise it rebuilds a local worker tag when
the sandbox is Docker. `RATCHET_AUTO_ROLLBACK=1` rolls back automatically on a failed smoke.

## Rollback

```bash
sudo /opt/ratchet/deploy/vm/rollback.sh                   # newest PASS/ok row that is not current
sudo /opt/ratchet/deploy/vm/rollback.sh <sha>             # a specific commit
sudo /opt/ratchet/deploy/vm/rollback.sh --with-restore 2026-10-27 <sha>   # the old code needs the old DB
```

Migrations are forward-only. If the older code can't run on the newer schema, restore the
nightly backup taken before the bad deploy along with the rollback (`--with-restore`).

## Backups and restore

```bash
sudo systemctl start ratchet-backup.service               # on demand; same as the 03:00 UTC timer
ls -lh /var/backups/ratchet/
sudo /opt/ratchet/deploy/vm/restore.sh 2026-10-25         # local file first, else object storage
```

- A backup holds `data/` (SQLite copied with the online backup API) and `skills/`. It does
  not hold `data/sessions/`, the PII key or `/etc/ratchet/env`.
- Backups are uploaded with `aws s3 cp --endpoint-url $NEBIUS_S3_ENDPOINT` when
  `RATCHET_BACKUP_S3_BUCKET` (or `NEBIUS_S3_BUCKET`) and the S3 key are set. They are kept
  14 days, locally and in the bucket.
- `restore.sh` moves the current state to `/srv/ratchet/pre-restore-<ts>/`. Delete that
  directory once you've confirmed the restore.

!!! danger "Keep the PII key safe separately"
    The PII token map is encrypted with `/srv/ratchet/pii.key`, and backups deliberately
    leave the key out. Store the key somewhere else. Without it, the tokens in restored
    traces can't be mapped back to the original values.

## Rotate secrets

| Secret | How | Downtime |
| --- | --- | --- |
| Demo action token | Nothing to do. It is `HMAC(secret, UTC date)[:24]`, so it changes daily with no restart. Yesterday's token is accepted until 01:00 UTC | none |
| `RATCHET_ACTION_TOKEN_SECRET` | Weekly by `ratchet-token-rotate.timer`, or `sudo deploy/vm/rotate-action-secret.sh`. This invalidates every token issued so far; open pages re-fetch `/meta` | ~3 s restart |
| `NEBIUS_TOKEN_FACTORY_KEY`, `TAVILY_API_KEY`, Nebius S3 keys | Create the new key in the provider console, `sudoedit /etc/ratchet/env`, `sudo systemctl restart ratchet-proxy ratchet-orchestrator` (aliases of `ratchet-serve`), check `ratchet doctor`, then revoke the old key | ~3 s restart |
| `NEBIUS_API_KEY` (an IAM token; it expires after 12 hours) | Automatic. `ratchet-nebius-token.timer` mints a new token from the service account in `/etc/ratchet/nebius-sa.json` every 4 hours and at boot, rewrites only that line of `/etc/ratchet/env`, and restarts `ratchet-serve` when the token changed and `RATCHET_JOB_RUNNER=nebius`. Check it with `sudo deploy/vm/nebius-token.sh status` | ~3 s restart every 4 hours, Jobs hosts only |
| Nebius service-account key | Generate a new one with `nebius iam auth-public-key generate --service-account-id <sa-id> --output nebius-sa.json`, copy it over `/etc/ratchet/nebius-sa.json` (root, 0600), run `sudo systemctl start ratchet-nebius-token`, then delete the old public key | as above |

To pause the weekly rotation, for example during a judging window, run
`sudo systemctl disable --now ratchet-token-rotate.timer`. Re-enable it afterwards.

## Replayed mode: zero spend, no models

```bash
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && env_set RATCHET_SIMULATE_MODE replayed'
sudo systemctl restart ratchet-serve
# back to live:
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && env_set RATCHET_SIMULATE_MODE live'
sudo systemctl restart ratchet-serve
```

Replayed mode serves the stored verdicts under `scenarios/recorded/<k>/` through the same UI,
with a **Recorded run** badge. Record them first, on a host running live models, with
`ratchet simulate <k> --record-fixture` for k = 1, 2, 3, and commit them. Rehearse the switch
once before you need it.

Related switches:

- `RATCHET_JOB_RUNNER=local` runs replays on the VM instead of Serverless Jobs.
- `RATCHET_SIMULATE_ENABLED=false` hides Simulate.
- `RATCHET_DAILY_USD_CAP=<n>` raises or lowers the daily cap.

## Incident playbook

| Symptom | First response (≤ 5 min) | Fallback |
| --- | --- | --- |
| URL unreachable, or an uptime alert | `sudo deploy/vm/restart.sh` restarts the app, reloads Caddy (and, with the tunnel edge, restarts the connector), and checks `/healthz` and the TLS expiry. `--all` also restarts Caddy and Docker | `sudo deploy/vm/failover.sh fallback on` serves a static page with the video. If the VM is gone: `deploy/vm/failover.sh dns <standby-ip>`, which moves the A records with the caddy edge and, with the tunnel edge, prints the takeover (start the connector on the standby with the same token, stop it on the old VM; no DNS change). If the app is broken but a standby is up: `sudo deploy/vm/failover.sh proxy-to <standby-private-ip>:8080` |
| Demo down but the local `/healthz` is fine (tunnel edge) | `sudo deploy/vm/edge-tunnel.sh status` (ready must be 200), `journalctl -u ratchet-tunnel -n 50`, and the tunnel's public hostname in Cloudflare | take over on the standby VM |
| Simulate hangs at "replaying" | `env_set RATCHET_JOB_RUNNER local`, then restart `ratchet-serve`. Check `journalctl -u ratchet-serve` for `replay.*` events, and `docker ps` for stuck `ratchet-worker-*` containers | `RATCHET_SIMULATE_MODE=replayed` |
| Token Factory errors (502s, `upstream_error`) | Check the provider's status page and run `ratchet doctor`. By design, verdicts degrade to `uncertain` / WARN | `RATCHET_SIMULATE_MODE=replayed` |
| Tavily errors | Nothing. Drift checks return `drift_unavailable`, and the UI shows the badge | same |
| Reports of state leaking between sessions | Check the cookie-keyed sessions (`data/sessions/`), then restart `ratchet-serve` | Set `RATCHET_SIMULATE_MAX_SESSIONS=1` (everyone queues) and restart |
| Spend cap hit (`429 budget_exceeded`) | Raise `RATCHET_DAILY_USD_CAP` by $5, then restart | `RATCHET_SIMULATE_MODE=replayed` costs nothing |
| Disk above 80 % (alert) | `ratchet_cli purge --before <date>` on non-demo traces; `journalctl --vacuum-time=3d` | Grow the disk in the Nebius console |
| Spend surge above $5/h (alert) | `ratchet_cli metrics --since 1d` to find the purpose; lower `RATCHET_PURPOSE_USD_CAPS` | replayed mode |
| Bad deploy | `sudo deploy/vm/rollback.sh` | `--with-restore <date>` |
| TLS expired or not issued (caddy edge) | `journalctl -u caddy -n 100`. DNS must resolve to the VM, and ports 80/443 must be open in the security group | `failover.sh fallback on` does not help with TLS; fix DNS first |

After any incident, write down what happened, when, and the fix. For deploys and
restores, add the row to `docs/DEPLOYS.md`.

## Metrics and alerts

`GET /metrics` on port 8080 serves Prometheus text format. `GET /healthz` returns `{ok,
components}`, with 200 when `ok` is true and 503 when it isn't. `ok` follows the database
and the probes `ratchet serve` wires in, such as the proxy.

| Metric | Type | Labels |
| --- | --- | --- |
| `ratchet_model_calls_total` | counter | tier, purpose, status |
| `ratchet_model_usd_total` | counter | tier, purpose |
| `ratchet_model_prompt_tokens_total` | counter | tier, purpose |
| `ratchet_model_cached_prompt_tokens_total` | counter (prompt-cache hits; over `ratchet_model_prompt_tokens_total` it is the cache share) | tier, purpose |
| `ratchet_model_latency_ms` | histogram | tier, purpose |
| `ratchet_cassette_events_total` | counter | kind |
| `ratchet_api_requests_total` | counter | route, method, status |
| `ratchet_api_request_duration_ms` | histogram | route |
| `ratchet_edits_total` | counter (from the verdicts table at scrape time) | decision |
| `ratchet_simulate_runs_total` | counter (from the database at scrape time) | scenario, status |
| `ratchet_up` | gauge | component (`dashboard_api`, `db`, `orchestrator`, `simulate`) |

`ratchet-alerts.timer` runs `deploy/vm/alerts.sh` every 2 minutes. Alerts go to
`RATCHET_NOTIFY_WEBHOOK` (Slack-compatible `{"text": …}`) and the journal, at most once per
30 minutes per kind, with a recovery message when the condition clears:

| Alert | Condition | Response |
| --- | --- | --- |
| Demo down | the local `/healthz` fails 2 runs in a row (about 2–4 min) | `restart.sh`, then `failover.sh` |
| Disk | the `/srv/ratchet` filesystem is more than 80 % used | `ratchet purge --before <date>` |
| Spend surge | `ratchet_model_usd_total` rises more than $5 within 1 hour | lower the purpose caps |

Simulate failures show up in `ratchet metrics --demo`, not in these alerts. The external
uptime monitor is separate, and the `uptime.yml` GitHub workflow is a 5-minute backup probe.

## Product metrics

`ratchet metrics --since 7d` prints the product table from the events table, and
`ratchet metrics --demo` prints the anonymous demo funnel. The **north-star** metric is
*protected edits*: skill edits that got a verdict backed by at least one blessed trace. The
table also reports blessed traces, the decision mix, block-precision and false-alarm proxies,
the drift share, edit-to-verdict time, cost per verdict against the counterfactual, the
uncertain rate, and evidence quality.

## Logs and retention

- Ratchet logs JSON lines, one per event, with no content, arguments or secrets. On the VM,
  journald keeps them 7 days (`/etc/systemd/journald.conf.d/ratchet.conf`).
- Caddy access logs (`/var/log/caddy/ratchet-access.log`) leave out query strings, request
  headers and cookies. They mask client IPs to /16 (IPv4) and /32 (IPv6), roll at 50 MiB,
  and are kept 7 days.
- `RATCHET_DEBUG=true` writes content to a local file only (`debug-content.log`). Never
  enable it on a public host.
- `ratchet logs --edit <id>` filters events and worker logs for one edit.
- Traces and cassettes are kept until you delete them:
  `ratchet purge --before <YYYY-MM-DD>` deletes live and replay traces older than the date,
  and goldens only with `--include-golden`.

## Local Docker

```bash
docker compose -f deploy/docker-compose.yml up --build control       # simulator, no keys
docker compose up control worker-local                               # the root compose.yaml spelling
docker compose -f deploy/docker-compose.yml run --rm control doctor  # any ratchet command
docker compose -f deploy/docker-compose.yml down                     # keeps the ratchet-srv volume
docker compose -f deploy/docker-compose.yml down -v                  # also deletes data and skills
```

| Image | Dockerfile | Notes |
| --- | --- | --- |
| `ratchet-control` | `deploy/control.Dockerfile` | Node 24 builds the dashboard; Python 3.11-slim with `uv sync --frozen --no-dev`; runs as user 10001; health check on `/healthz`; the entrypoint migrates, seeds and runs `ratchet serve`. `--target control-docker` adds the Docker CLI |
| `ratchet-worker` | `worker/Dockerfile` | Used by Serverless Jobs and the Docker runner. CI enforces a size limit under 2 GB |

### Sandbox network

With `RATCHET_SANDBOX=docker`, each replay runs in a container on the internal bridge
`ratchet-sandbox` (172.30.0.0/24, `--internal`, inter-container traffic off). The worker binds
its proxy and mock-tool sidecars on the gateway 172.30.0.1, so those are the only endpoints a
sandbox can reach, and only the sidecar holds the Token Factory key. Create or check the
network with `deploy/sandbox-network.sh` (bootstrap does this).

!!! warning "Docker ≥ 28"
    Confirm the gateway address is bound on the host: `ip addr show` should list 172.30.0.1
    on the bridge. If it doesn't, the sidecars can't bind it, and replays fail with
    `sandbox_failed`. Use replayed mode while you fix it. This is not yet verified live.
