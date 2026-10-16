# Runbook: the Ratchet demo host

Operating the public host, as commands. Three names, and two ways to put them on the
internet, chosen by `RATCHET_EDGE` in `/etc/ratchet/env` (`bootstrap.sh --edge`):

| Name | What | `tunnel` (Cloudflare in front) | `caddy` (default: one public VM) |
| --- | --- | --- | --- |
| `ratchetci.dev` | static product page, `/docs/` (MkDocs site), `/bench/` (the benchmark report) | Cloudflare static assets, Worker `ratchetci-site`, deployed by `.github/workflows/site.yml` on every push to `main` that touches them | Caddy on the VM, `/var/www/ratchet-site`, rebuilt by every deploy |
| `www.ratchetci.dev` | 301 to `https://ratchetci.dev` | a zone Redirect Rule | Caddy (also `RATCHET_ALT_ADDRESSES`) |
| `demo.ratchetci.dev` (`RATCHET_PUBLIC_URL`) | the dashboard, API and SSE stream, public mode; static fallback when the app is down | Cloudflare Tunnel -> `ratchet-tunnel.service` on the VM -> Caddy on `127.0.0.1:80` -> `ratchet-serve` | Caddy on 80/443 (ACME) -> `ratchet-serve` on 127.0.0.1:8080 |

In tunnel mode the VM has no inbound port but SSH (ufw on by default, 22/tcp only), holds no
certificate, and moving the demo to another VM needs no DNS change. DNS records, the tunnel's
remote configuration and the Redirect Rule: `deploy/vm/dns.md`. Every procedure is a script
in `deploy/vm/` (run as root on the VM) or `deploy/` (local Docker). Scripts log to stderr with UTC timestamps, take a shared
lock (`/var/lib/ratchet-ops/ops.lock`), are idempotent, and post to `RATCHET_NOTIFY_WEBHOOK`
when it is set.

## Layout on the VM

| Path | What | Owner / mode |
| --- | --- | --- |
| `/opt/ratchet` | git checkout, `.venv` (Python 3.11 via uv), `dashboard/dist` | `ratchet`, read-only to the service |
| `/srv/ratchet/data`, `/srv/ratchet/skills` | traces, cassettes, SQLite (WAL), verdicts; the skills git repo | `ratchet` 0750; the service's only writable path |
| `/srv/ratchet/pii.key` | Fernet key for `data/pii.map.enc` (kept out of `data/` and out of backups) | `ratchet` 0600 |
| `/srv/ratchet/tmp` | `TMPDIR` for replays (visible to the Docker daemon) | `ratchet` |
| `/etc/ratchet/env` | every setting and secret (systemd `EnvironmentFile`) | `root` 0600 |
| `/var/lib/ratchet-ops/deploys.tsv` | deploy ledger; rollback reads it | `root` |
| `/var/backups/ratchet` | nightly tarballs, 14 days | `root` 0700 |
| `/var/www/ratchet-fallback` | static fallback page (demo host) | `root` |
| `/var/www/ratchet-site` | caddy mode: the apex site, symlink to the live release in `/var/www/ratchet-site.d/` (two kept) | `root` |
| `/etc/ratchet/cloudflared-token` | tunnel mode: the Cloudflare Tunnel token (`edge-tunnel.sh set-token`; read by systemd as a credential, never logged) | `root` 0600 |
| `/var/log/caddy/` | Caddy access logs; the directory and every file in it belong to `caddy` (`caddy_log_dir` in `lib.sh`; Caddy does not start otherwise) | `caddy` |
| `/etc/caddy/ratchet.d/` | generated Caddy fragments: `site/docs-csp.caddy` (hashes of the docs theme's inline scripts), `redirects/alt-domains.caddy` | `root` |
| `/etc/ratchet/nebius-sa.json` | Nebius service-account credentials (Serverless Jobs only; you copy it) | `root` 0600 |
| `/opt/ratchet/nebius/bin/nebius`, `/var/lib/ratchet-ops/nebius` | Nebius CLI (pinned) and its config (profile `ratchet-sa`) | `root`; config 0700 |

Units: `ratchet-serve.service` (one `ratchet serve` process: dashboard + API on
127.0.0.1:8080, proxy on 8081, mock tools on 8082, orchestrator and watcher; aliases
`ratchet-proxy`, `ratchet-mocktools`, `ratchet-orchestrator`, `ratchet-dashboard`),
`caddy.service` (caddy mode: TLS, ports 80 and 443; tunnel mode: plain HTTP on
127.0.0.1:80 only), `ratchet-tunnel.service` (tunnel mode only: `cloudflared tunnel run`, a
dynamic user, the token as a systemd credential, readiness on `127.0.0.1:20241/ready`), and
the timers `ratchet-backup.timer` (03:00 UTC),
`ratchet-token-rotate.timer` (Mon 04:30 UTC), `ratchet-alerts.timer` (every 2 min) and
`ratchet-nebius-token.timer` (every 4 h and at boot; skipped without the credentials file).

## Status at a glance

```bash
systemctl status ratchet-serve caddy --no-pager
systemctl list-timers 'ratchet-*' --no-pager
curl -fsS http://127.0.0.1:8080/healthz; echo
sudo deploy/vm/failover.sh status                         # edge mode, fallback, upstream, health
sudo deploy/vm/edge-tunnel.sh status                      # tunnel mode: cloudflared, unit, ready, token file
sudo deploy/vm/nebius-token.sh status                     # Jobs hosts: CLI, profile, last refresh
curl -fsSI https://ratchetci.dev/ | head -n 1; curl -fsSI https://ratchetci.dev/docs/ | head -n 1
journalctl -u ratchet-serve -n 100 --no-pager -o cat      # JSON lines, 7-day retention
journalctl -u ratchet-serve --since -1h -o cat | grep '"level":"ERROR"'
tail -n 20 /var/lib/ratchet-ops/deploys.tsv
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && ratchet_cli metrics --since 1d --demo'
```

CLI on the VM, with the service's user, env file and sandboxing:

```bash
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && ratchet_cli doctor'
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && ratchet_cli logs --edit <edit_id>'
```

## First install

```bash
# on a fresh Ubuntu 24.04 Nebius CPU VM (2 vCPU / 8 GB)
git clone https://github.com/<org>/ratchet /tmp/ratchet
# Serverless Jobs only: the service-account credentials, before bootstrap (or later, see below)
sudo install -d -m 0700 /etc/ratchet
sudo install -m 0600 -o root -g root nebius-sa.json /etc/ratchet/nebius-sa.json
# tunnel mode: the tunnel token first (deploy/vm/dns.md, "The tunnel"); nothing is echoed
sudo /tmp/ratchet/deploy/vm/edge-tunnel.sh set-token < token.txt && shred -u token.txt
sudo /tmp/ratchet/deploy/vm/bootstrap.sh --edge tunnel --public-url https://demo.ratchetci.dev \
     --site-url https://ratchetci.dev --repo https://github.com/<org>/ratchet --ref main
# caddy mode instead: DNS A records pointing at the VM first (deploy/vm/dns.md), then the same
# command with --edge caddy (or without --edge)
sudoedit /etc/ratchet/env        # NEBIUS_TOKEN_FACTORY_KEY, TAVILY_API_KEY, RATCHET_UPSTREAM=live, ...
sudo systemctl restart ratchet-serve
```

Tunnel mode, once per Cloudflare account (`deploy/vm/dns.md` has the steps and API calls):

1. **Tunnel**: Zero Trust -> Networks -> Tunnels -> create `ratchet-demo` (Cloudflared), copy
   its token into `token.txt` (as above). Public hostname `demo.ratchetci.dev` -> `HTTP`
   `127.0.0.1:80`; the catch-all rule stays `http_status:404`. This creates the CNAME.
2. **Static site**: the one-time steps in the header of `.github/workflows/site.yml`: API
   token (Workers Scripts: Edit, Workers Routes: Edit, DNS: Edit), repository secrets
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, delete old A/CNAME records for the
   apex and `www`, run the workflow on `main`. wrangler attaches `ratchetci.dev` and
   `www.ratchetci.dev` as custom domains and creates their records.
3. **www -> apex**: the zone Redirect Rule (dashboard template "Redirect from WWW to root").

The token is a credential for the demo hostname: `edge-tunnel.sh` never prints it, the unit
reads it through `LoadCredential=`, and `edge-tunnel.sh status` shows only its size and mode.
Without the token file, bootstrap installs `cloudflared` and leaves `ratchet-tunnel` stopped;
write the token, then `sudo deploy/vm/edge-tunnel.sh install`. Switch an existing host
between modes with `sudo deploy/vm/deploy.sh <sha> --edge tunnel|caddy` (or re-run
bootstrap with `--edge`); switching to `caddy` stops and disables the connector.

`--public-url` is the demo (`RATCHET_PUBLIC_URL`, what the app and the uptime probe use);
`--site-url` is the apex (`RATCHET_SITE_URL`, ops scripts only; in tunnel mode only checked).
Both, and the edge, are written to `/etc/ratchet/env` and to the Caddy drop-in
(`RATCHET_EDGE`, `RATCHET_DEMO_ADDRESS`, `RATCHET_SITE_ADDRESS`).
Alt domains that should redirect to the apex: `RATCHET_ALT_ADDRESSES="ratchetci.com
www.ratchetci.com"` in the env file, then re-run bootstrap.

`bootstrap.sh` is idempotent: re-run it to converge a host (it never overwrites values in
`/etc/ratchet/env`, only appends keys new in `deploy/vm/env.template`). Firewall: `--ufw
on|off` (`RATCHET_ENABLE_UFW` in the env file); the default is on in tunnel mode (22/tcp
only) and off in caddy mode (on: 22, 80, 443). It is configured right after the env file, so
a later failure never leaves it undone. As a variable it only works inside sudo
(`sudo RATCHET_ENABLE_UFW=1 bootstrap.sh`): `RATCHET_ENABLE_UFW=1 sudo ...` is dropped by
sudo. SSH on a port other than 22: allow it first (`ufw allow <port>/tcp`). Other opt-ins:
`RATCHET_SKIP_SEED=1`, `RATCHET_SKIP_WORKER_IMAGE=1`. Then set up the external monitor:
`deploy/vm/uptime.md`.

## Deploy

```bash
# 1. wait for CI green on <sha> (CI also pushes ghcr.io/<org>/ratchet-worker:<sha> on main)
sudo /opt/ratchet/deploy/vm/deploy.sh <sha>
```

The script checks the GitHub check runs for `<sha>` (`--skip-ci-check` to bypass;
`RATCHET_REQUIRE_CI=1` makes an unreachable API fatal), checks out, runs `uv sync --frozen
--no-dev`, `npm ci && npm run build`, `ratchet db migrate`, restarts `ratchet-serve`, waits
for `/healthz`, then runs `ratchet doctor` and `ratchet simulate 1 --headless` (BLOCK within
90 s), and appends the result to the ledger and `docs/DEPLOYS.md`. In caddy mode it also
rebuilds the apex site (MkDocs into `/docs/` from a separate docs venv,
`RATCHET_BENCH_REPORT_DIR` rendered into `/bench/`, `deploy/vm/www`) and switches
`/var/www/ratchet-site` to it atomically; a failed docs build keeps the previous `/docs/` and
never fails the deploy. In tunnel mode the apex is not on the VM: `.github/workflows/site.yml`
builds the same tree (`render_site.py --cloudflare`, which adds `_headers` and `_redirects`)
and runs `wrangler deploy` on every push to `main` that touches the docs, the benchmark
results or the page. Both modes: the Caddyfile is installed when it validates (as the
`caddy` user, so the access-log files stay Caddy's), then `systemctl reload caddy`. With
`RATCHET_WORKER_IMAGE_REPO=ghcr.io/<org>/ratchet-worker` in the env file it pulls and pins the
CI-built worker for `<sha>`; otherwise a local worker tag is rebuilt when the sandbox is
Docker. `RATCHET_AUTO_ROLLBACK=1` rolls back automatically on a failed smoke.

## Rollback

```bash
sudo /opt/ratchet/deploy/vm/rollback.sh                    # newest PASS/ok row that is not current
sudo /opt/ratchet/deploy/vm/rollback.sh <sha>              # a specific commit
sudo /opt/ratchet/deploy/vm/rollback.sh --with-restore 2026-10-27 <sha>   # schema needs the old DB
```

Migrations are forward-only. If the older code cannot run on the newer schema, restore the
nightly backup taken before the bad deploy along with the rollback (`--with-restore`).

## Backup and restore

```bash
sudo systemctl start ratchet-backup.service                # on demand (same as the 03:00 UTC timer)
ls -lh /var/backups/ratchet/
sudo /opt/ratchet/deploy/vm/restore.sh 2026-10-25          # local file, else object storage
```

Backups hold `data/` (SQLite copied with the online backup API) and `skills/`, without
`data/sessions/`, the PII key or `/etc/ratchet/env`. They are uploaded with `aws s3 cp
--endpoint-url $NEBIUS_S3_ENDPOINT` when `RATCHET_BACKUP_S3_BUCKET` (or `NEBIUS_S3_BUCKET`)
and the S3 key are set, and kept 14 days locally and in the bucket. `restore.sh` moves the
current state to `/srv/ratchet/pre-restore-<ts>/` (delete it once the restore is confirmed).
Rehearse once before Oct 26 and record the `restore` row in `docs/DEPLOYS.md`.

## Rotate secrets

| Secret | How | Downtime |
| --- | --- | --- |
| Demo action token | nothing to do: it is `HMAC(secret, UTC date)[:24]`, so it changes daily with no restart; yesterday's token is accepted for 1 h after midnight | none |
| `RATCHET_ACTION_TOKEN_SECRET` | weekly by `ratchet-token-rotate.timer`, or `sudo deploy/vm/rotate-action-secret.sh`; kills every token issued so far (open pages re-fetch `/meta`) | ~3 s restart |
| `NEBIUS_TOKEN_FACTORY_KEY`, `TAVILY_API_KEY`, Nebius S3 keys | create the new key in the provider console, `sudoedit /etc/ratchet/env`, `sudo systemctl restart ratchet-proxy ratchet-orchestrator` (aliases of `ratchet-serve`), check `ratchet doctor`, then revoke the old key | ~3 s restart |
| Cloudflare Tunnel token (`/etc/ratchet/cloudflared-token`, tunnel mode) | Zero Trust -> Networks -> Tunnels -> `ratchet-demo` -> Refresh token (or the API's token endpoint), then `sudo deploy/vm/edge-tunnel.sh set-token < token.txt` on the VM (restarts the connector) and on the standby; the old token stops working | ~2 s reconnect |
| `CLOUDFLARE_API_TOKEN` (repository secret for site.yml) | roll it in My Profile -> API Tokens, update the repository secret, re-run the Site workflow | none |
| `NEBIUS_API_KEY` (IAM token, 12 h) | automatic: `ratchet-nebius-token.timer` (below) | ~3 s restart every 4 h, only with `RATCHET_JOB_RUNNER=nebius` |
| Nebius service-account key (`/etc/ratchet/nebius-sa.json`) | `nebius iam auth-public-key generate --service-account-id <sa-id> --output nebius-sa.json` on a workstation, copy it over the old file (root 0600), `sudo systemctl start ratchet-nebius-token` (the profile is recreated when the file changes), then delete the old public key of the service account | as above |

### Nebius IAM token refresh (Serverless Jobs)

The Jobs runner authenticates with `NEBIUS_API_KEY`, an IAM token that expires after 12 h.
`ratchet-nebius-token.timer` runs `deploy/vm/nebius-token.sh` at boot and every 4 h (00, 04,
08, ... UTC): it uses the Nebius CLI at `/opt/ratchet/nebius/bin/nebius` (installed by
bootstrap with the official script, version pinned by `NEBIUS_CLI_VERSION` in the script), a
CLI profile `ratchet-sa` made from `/etc/ratchet/nebius-sa.json` (endpoint `api.nebius.cloud`,
parent `NEBIUS_PROJECT_ID`), and `nebius --profile ratchet-sa iam get-access-token`. It
rewrites only the `NEBIUS_API_KEY=` line of `/etc/ratchet/env` (atomically, mode kept, the
token never logged) and restarts `ratchet-serve` only if the token changed and
`RATCHET_JOB_RUNNER=nebius`. Hosts without the credentials file skip it.

```bash
sudo install -m 0600 -o root -g root nebius-sa.json /etc/ratchet/nebius-sa.json   # once
sudo systemctl start ratchet-nebius-token        # refresh now
sudo deploy/vm/nebius-token.sh status            # CLI version, profile, last run, token set
journalctl -u ratchet-nebius-token -n 20 -o cat
```

The service account needs the role that creates and reads AI Jobs in the project (and the
object-storage access the runner uses). Give the standby VM its own copy of the file.

Pause the weekly rotation during the judging window: `sudo systemctl disable --now
ratchet-token-rotate.timer` (re-enable afterwards).

## Switch to replayed mode (zero spend, no models)

```bash
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && env_set RATCHET_SIMULATE_MODE replayed'
sudo systemctl restart ratchet-serve
# back to live:
sudo bash -c 'source /opt/ratchet/deploy/vm/lib.sh && env_set RATCHET_SIMULATE_MODE live'
sudo systemctl restart ratchet-serve
```

Replayed mode serves the stored verdicts under `scenarios/recorded/<k>/` through the same UI
with a "Recorded run" badge. Record them first, on a host running live models:
`ratchet simulate <k> --record-fixture` for k = 1, 2, 3, and commit them. Rehearse the switch
once before the demo window.

Related switches: `RATCHET_JOB_RUNNER=local` (replays on the VM instead of Serverless Jobs),
`RATCHET_SIMULATE_ENABLED=false` (hide Simulate), `RATCHET_DAILY_USD_CAP=<n>` (raise it only
deliberately, one step at a time).

## Incident playbook (Oct 29 to the end of judging)

| Symptom | First response (≤ 5 min) | Fallback |
| --- | --- | --- |
| URL unreachable / uptime alert | `sudo deploy/vm/restart.sh` (restarts the app, reloads Caddy, in tunnel mode restarts the connector, checks `/healthz` and the TLS expiry); `--all` also restarts Caddy and Docker | `sudo deploy/vm/failover.sh fallback on` (static page with the video); VM gone: `deploy/vm/failover.sh dns <standby-ip>` (caddy mode: move the A records; tunnel mode: prints the takeover, below); app broken but a standby is up: `sudo deploy/vm/failover.sh proxy-to <standby-private-ip>:8080` |
| Demo down, local `/healthz` fine (tunnel mode) | `sudo deploy/vm/edge-tunnel.sh status` (ready must be 200); `journalctl -u ratchet-tunnel -n 50`; the tunnel's public hostname in Zero Trust must be `demo.ratchetci.dev` -> `http://127.0.0.1:80`; Cloudflare status page | `sudo deploy/vm/failover.sh fallback on` does not help here (the fallback is behind the tunnel); take over on the standby |
| Simulate hangs at "replaying" | `env_set RATCHET_JOB_RUNNER local`, restart `ratchet-serve`; check `journalctl -u ratchet-serve` for `replay.*` events; `docker ps` for stuck `ratchet-worker-*` | `RATCHET_SIMULATE_MODE=replayed` (above) |
| Token Factory errors (502s, `upstream_error`) | check the provider's status page; `ratchet doctor`; verdicts degrade to `uncertain`/WARN by design | `RATCHET_SIMULATE_MODE=replayed` |
| Tavily errors | nothing: drift checks return `drift_unavailable` and the UI shows the badge | same |
| Judges report state leaking between sessions | check cookie-keyed sessions (`data/sessions/`); restart `ratchet-serve` | set `RATCHET_SIMULATE_MAX_SESSIONS=1` (queue) and restart |
| Spend cap hit (429 `budget_exceeded`) | raise `RATCHET_DAILY_USD_CAP` by $5 (the operator's call), restart | `RATCHET_SIMULATE_MODE=replayed` costs nothing |
| Disk > 80 % (alert) | `ratchet_cli purge --before <date>` on non-demo traces; `journalctl --vacuum-time=3d` | grow the disk in the Nebius console |
| Spend surge > $5/h (alert) | `ratchet_cli metrics --since 1d` to find the purpose; lower `RATCHET_PURPOSE_USD_CAPS` | replayed mode |
| Simulate failing (alert: ≥ 2 of the last 5 runs failed) | `ratchet_cli logs --edit <edit_id>` (or `GET /api/v1/edits/<id>/logs`); `ratchet_cli simulate <k> --headless --repeat 3` to reproduce | `RATCHET_SIMULATE_MODE=replayed` |
| Jobs stalled (alert: a batch without a heartbeat for 120 s) | the orchestrator marks it `stalled`, cancels it and retries once on the local runner (`retried`); if it repeats, `env_set RATCHET_JOB_RUNNER local` and restart; `docker ps` for stuck `ratchet-worker-*` | replayed mode |
| Bad deploy | `sudo deploy/vm/rollback.sh` | `--with-restore <date>` |
| TLS expired / not issued (caddy mode) | `journalctl -u caddy -n 100`: DNS must resolve to the VM without a proxy, ports 80/443 open in the security group (`deploy/vm/dns.md`) | `failover.sh fallback on` does not help TLS; fix DNS first. Tunnel mode: Cloudflare holds and renews the certificates |
| Caddy does not start: "opening log writer" | a log file in `/var/log/caddy` is not owned by `caddy` (left by a validation as root): `sudo chown -R caddy:caddy /var/log/caddy && sudo systemctl restart caddy`; deploy and bootstrap do this themselves (`caddy_log_dir`) | — |
| `ratchetci.dev` down, demo fine (caddy mode) | `ls -l /var/www/ratchet-site` (symlink to a release); `journalctl -u caddy -n 50`; re-run `sudo deploy/vm/deploy.sh <current sha> --skip-ci-check` to rebuild it | point the symlink at the previous release in `/var/www/ratchet-site.d/` (`ln -sfn <dir> /var/www/ratchet-site`) |
| `ratchetci.dev` down (tunnel mode) | the last run of the Site workflow; dashboard -> Workers & Pages -> `ratchetci-site` -> Deployments, Domains (both custom domains active) | roll back to the previous deployment there (or `wrangler rollback`), or re-run the workflow on a good commit |
| Jobs fail with 401/403 (`replay.*` events) | `sudo deploy/vm/nebius-token.sh status`; `sudo systemctl start ratchet-nebius-token`; `journalctl -u ratchet-nebius-token` | `env_set RATCHET_JOB_RUNNER local`, restart |

### Failover to the standby VM

| | caddy mode | tunnel mode |
| --- | --- | --- |
| Prepare | snapshot -> second VM; `restore.sh`, `deploy.sh` there (`failover.sh --help`) | the same; then **on the standby** `sudo systemctl disable --now ratchet-tunnel` at once: the snapshot carries the token and the enabled unit, and two connectors with one token share the traffic |
| Take over | `deploy/vm/failover.sh dns <standby-ip>`: the apex and `demo` A records move to the standby (or `RATCHET_DNS_HOOK`); the standby's Caddy gets certificates once they resolve (TTL 60 during judging) | no DNS change: on the standby `sudo deploy/vm/edge-tunnel.sh install` (same token), then `sudo systemctl disable --now ratchet-tunnel` on the old VM if it is still up. `failover.sh dns <ip>` prints these steps |
| Apex | moves with the A records | not affected (Cloudflare) |

Back to the primary: the same steps the other way round. Record each switch in
`docs/DEPLOYS.md` (kind `ops`).

### Static site headers (tunnel mode)

`render_site.py --cloudflare` writes one `_headers` rule for `/*`: HSTS, `nosniff`,
`X-Frame-Options: DENY`, the referrer and permissions policies, COOP, and the apex CSP
(identical to the Caddyfile's). `script-src` is `'self'` plus the SHA-256 hashes of the
MkDocs theme's inline scripts, computed from the built docs on every run, site-wide rather
than for `/docs/*` only: Cloudflare joins two matching rules' values for one header with a
comma, which browsers treat as two policies that must both pass, so a stricter `/*` policy
would block the docs scripts. A hash allows only that exact script and no other page has
inline scripts, so nothing else is granted; `'unsafe-inline'` is never used. `_redirects`:
`/docs` and `/bench` -> trailing slash (301); `404.html` comes from `deploy/vm/www`, and
`/docs/` has MkDocs' own (`not_found_handling: 404-page` serves the nearest one).

After any incident: a note of what happened, when and the fix and, for deploys or
restores, the row in `docs/DEPLOYS.md`.

## Metrics (three Prometheus registries)

`ratchet serve` runs three apps, each with its own `/metrics` (Prometheus text). Only the
dashboard port is published; the other two are on 127.0.0.1 inside the host. Alerts
(`deploy/vm/alerts.sh` → `alerts_check.py`) scrape the dashboard and the proxy.

| Registry | URL | Metrics |
| --- | --- | --- |
| Dashboard + API | `http://127.0.0.1:8080/metrics` (`RATCHET_LOCAL_URL`) | `ratchet_api_requests_total{route,method,status}`, `ratchet_api_request_duration_ms{route}`, `ratchet_edits_total{decision}`, `ratchet_simulate_runs_total{scenario,status}`, `ratchet_up{component}`, `ratchet_batches_stalled`, `ratchet_batch_stalls_total`, `ratchet_simulate_recent_failed`, `ratchet_simulate_recent_runs`; plus the orchestrator's series (same process): `ratchet_edit_duration_s{runner}`, `ratchet_stage_duration_ms{stage}`, `ratchet_jobs_total{runner,status}`, `ratchet_job_cold_start_s{runner}` |
| Capture proxy | `http://127.0.0.1:8081/metrics` (`RATCHET_PROXY_METRICS_URL`) | `ratchet_model_calls_total{tier,purpose,status}`, `ratchet_model_usd_total{tier,purpose}`, `ratchet_model_latency_ms{tier,purpose}` |
| Mock tools | `http://127.0.0.1:8082/metrics` | mock tool server counters (not alerted on) |

The edit, Simulate and stall series are derived from SQLite at scrape time (they survive
restarts); the orchestrator and proxy series are in-process and reset on restart (the spend
alert treats a counter drop as a restart). `ratchet_jobs_total` statuses: `ok`, `partial`,
`failed` (imported), `stalled`, `lost` (no result; retried), `submit_failed`. Product metrics
are JSON, not Prometheus: `GET /api/v1/metrics/product?since=7d` and, operator host
only, `GET /api/v1/metrics/demo?since=7d` (same rows as `ratchet metrics [--demo]`).

## Security limitations (known, accepted; ADR 0022)

What the 2026-10-16 review hardening does not cover, so an operator knows where the edges are:

| Area | Limitation | Why it is accepted / what limits it |
| --- | --- | --- |
| Rate limits, stream caps, Simulate per-client limit | In memory, per `ratchet serve` process; a restart resets them. Keyed by client IP, which is only right behind Caddy with `RATCHET_FORWARDED_ALLOW_IPS` set to the proxy. In tunnel mode Caddy takes the client address from `Cf-Connecting-Ip`, trusted only from loopback (cloudflared), and forwards it in `X-Forwarded-For` | One process per host; the daily USD cap is in SQLite and survives restarts |
| Sidecar `/tools/*/notify` | No admin token: it is the harness's own channel from inside the sandbox, so a token would live in the sandbox too | Accepts only local read tools and only for the one run that sidecar serves; `/log`, `/mode`, `/runs/{id}/steps` need the per-golden worker token |
| Dispatch allowance | Edits dispatched at the same moment each see the same remaining allowance | At most `RATCHET_MAX_EDITS_IN_FLIGHT` (2) edits overlap; the control-plane proxy still enforces the daily cap on every judge call |
| Secret redaction in streams | A secret split across separate SSE content deltas is not contiguous in the bytes, so it is not caught | Redaction also runs on the recorded step and the cache; the upstream never sees sandbox secrets |
| Public trace visibility cache | A verdict that lands is visible on `GET /traces/{id}` within ~1 s (miss refresh), cached up to 30 s per session | Bounded to 512 sessions; misses refresh at most once a second |
| `shell.run` | Six read-only commands in Python; `grep` supports basic regular expressions only | Defence in depth; the sandbox is the boundary (OpenShell not yet verified live) |
| Hermes toolsets | The allowlist (`--toolsets mcp-ratchet_mock,file,skills`) and the `mcp-<server>` toolset name are not yet verified against a live Hermes; the denylist stays as a second layer | Hermes is off by default (`RATCHET_HARNESS=minimal`) and refused with the process sandbox |
| Process sandbox | No isolation at all (ADR 0017) | Refused in public mode and for Hermes; dev only |

## Sandbox network (Docker sandbox)

Replays with `RATCHET_SANDBOX=docker` run each harness in a container on the internal bridge
`ratchet-sandbox` (172.30.0.0/24, `--internal`, ICC off). The worker binds its proxy and
mock-tool sidecars on the gateway 172.30.0.1, so they are the only endpoints a sandbox can
reach (no internet egress), and only the sidecar holds the Token Factory key: the sandbox
never sees it. Create or check it with `deploy/sandbox-network.sh` (bootstrap does). On
Docker ≥ 28 confirm the gateway address is bound on the host (`ip addr show` shows
172.30.0.1 on the bridge); if not, the sidecars cannot bind it, and replays fail with
`sandbox_failed`. Use replayed mode while that is fixed.

## Local Docker (development)

```bash
docker compose -f deploy/docker-compose.yml up --build control      # sim, no keys: http://127.0.0.1:8080
docker compose up control worker-local                              # same stack via the root compose.yaml
docker compose -f deploy/docker-compose.yml run --rm control doctor # any ratchet command
docker compose -f deploy/docker-compose.yml down                    # keeps the ratchet-srv volume
docker compose -f deploy/docker-compose.yml down -v                 # also deletes data and skills
```

Replays as sibling containers (docker runner + docker sandbox): see the header of
`deploy/docker-compose.docker-runner.yml`. It mounts the Docker socket, which makes the
control container root-equivalent on the host: local integration only.

## Images and CI

| Image | Dockerfile | Built by | Notes |
| --- | --- | --- | --- |
| `ratchet-control` | `deploy/control.Dockerfile` (context: repo root) | CI `build-control-image`; pushed to `ghcr.io/<org>/ratchet-control:<sha>` on main | node 24 builds `dashboard/dist`; python 3.11-slim + `uv sync --frozen --no-dev`; user 10001; `git` + `tini`; `HEALTHCHECK` on `/healthz`; entrypoint migrates, seeds (idempotent) and execs `ratchet serve`; `--target control-docker` adds the Docker CLI |
| `ratchet-worker` | `worker/Dockerfile` | CI `build-worker-image` (size gate < 2 GB); pushed as `ghcr.io/<org>/ratchet-worker:<sha>` on main | used by Serverless Jobs and the docker runner |

Workflows: `.github/workflows/ci.yml` (every PR and push: lint incl. shellcheck, hadolint,
actionlint, compose and Caddy validation; mypy; pytest with the 80 % gates on
`ratchet/judge` and `ratchet/replay`; security tests + `scripts/secret_scan.py`; schema drift;
dashboard; both images), `nightly.yml` (pytest `-m e2e` and `-m docker`; compose up + `simulate
1..3 --headless` + Playwright against the container), `live.yml` (manual, `live`
environment secrets, `RATCHET_DAILY_USD_CAP=2`), `uptime.yml` (5-minute probe, needs the
`RATCHET_PUBLIC_URL` repository variable), `site.yml` (the apex site in tunnel mode:
gen_docs, `mkdocs build --strict`, `render_site.py --cloudflare`, then `wrangler deploy` of
the static-assets Worker `ratchetci-site`; main only; secrets `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID`).

Secret scan locally (findings are redacted, never echoed):

```bash
uv run python scripts/secret_scan.py --git --untracked --env-file .env   # your real keys vs the tree
uv run python scripts/secret_scan.py --env-file .env var/data           # traces, cassettes, SQLite, logs
uv run python scripts/secret_scan.py --pii var/data/cassettes           # no raw PII in cassettes
```

## Logs and retention

JSON lines to journald, 7 days (`/etc/systemd/journald.conf.d/ratchet.conf`). Caddy access
logs at `/var/log/caddy/ratchet-access.log` (demo) and `ratchet-site-access.log` (apex): no
query strings, no request headers or cookies,
client IPs masked to /16 (IPv4) and /32 (IPv6), rolled at 50 MiB and kept 7 days. Content
logging (`RATCHET_DEBUG`) is never enabled on the public host.
