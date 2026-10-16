# External uptime monitor for the demo URL

The "demo down" alert is defined from *outside* the VM: `GET /healthz` fails twice in two
minutes. Confirm the monitor works before the demo URL is made public. Inside the VM, `ratchet-alerts.timer` covers disk and spend; the
`Uptime` GitHub workflow is a 5-minute backup probe. Neither replaces this monitor.

## What to monitor

| Monitor | URL | Check | Interval | Alert after |
| --- | --- | --- | --- | --- |
| Health | `https://demo.ratchetci.dev/healthz` | HTTP 200 (the body is `{"ok": true, ...}`) | 1 min | 2 consecutive failures |
| Landing | `https://demo.ratchetci.dev/` | HTTP 200 and the keyword `id="root"` | 5 min | 2 consecutive failures |
| Site | `https://ratchetci.dev/` | HTTP 200 and the keyword `never unlearns` | 5 min | 2 consecutive failures |
| TLS | `https://demo.ratchetci.dev` and `https://ratchetci.dev` | certificate valid for at least 14 more days | daily | first failure |

The apex (`ratchetci.dev`, with `/docs/` and `/bench/`) is static files served by the same
Caddy, so it stays up when the app is down; a Site alert means Caddy, TLS or DNS
(`deploy/vm/dns.md`), not the app.

When the app is down, Caddy serves the static fallback page for `/` (HTTP 502/503) and
answers `/healthz` with `503 upstream down`, so the Health monitor goes red even though
judges still see a page. Do not point a monitor at `/api/...` action endpoints: they are
rate-limited and need the action token.

## Setting it up (any provider)

Use any external HTTP monitor whose plan allows 1-minute checks: Better Stack Uptime,
UptimeRobot, Checkly, or a self-hosted Uptime Kuma on a machine other than the demo VM.
Free tiers often check only every 3 to 5 minutes, so check the plan's interval first.

1. Create an HTTP(S) monitor for `https://demo.ratchetci.dev/healthz`: method GET, timeout 10 s, follow
   redirects off, expect status 200. Check from at least two regions if the provider allows.
2. Confirmation: alert only after 2 failed checks in a row (about 2 minutes).
3. Notifications: the team channel (the same Slack/Telegram webhook as
   `RATCHET_NOTIFY_WEBHOOK`), plus email or SMS to whoever is on call during judging.
4. Add the Landing, Site and TLS monitors from the table.
5. Test it: `sudo deploy/vm/failover.sh fallback on` does **not** trip Health (`/healthz` is
   never masked). To see a real alert, run `sudo systemctl stop ratchet-serve` for about 3
   minutes, confirm the alert arrives, then `sudo deploy/vm/restart.sh` and confirm the
   recovery message.
6. Record the monitor's name and dashboard link in the deploy log (docs/DEPLOYS.md).

## When it fires

Follow `docs/RUNBOOK.md` → "Incident playbook": `restart.sh` first, then `failover.sh` within
5 minutes if the URL is still down.

## GitHub backup probe

Set the repository variable `RATCHET_PUBLIC_URL` to the demo URL, `https://demo.ratchetci.dev` (Settings → Secrets and variables → Actions →
Variables) to enable `.github/workflows/uptime.yml`. It runs every 5 minutes (GitHub may
delay scheduled runs under load) and fails the run, which emails the repo's watchers, when
`/healthz` fails twice 30 s apart. GitHub disables scheduled workflows in a repo with no
activity for 60 days; re-enable it in the Actions tab during the judging window.
