# Deploys

Every deploy, rollback, restore and bootstrap of the public demo host. The
VM's ledger (`/var/lib/ratchet-ops/deploys.tsv`) is the source of truth; `deploy/vm/deploy.sh`,
`rollback.sh`, `restore.sh` and `bootstrap.sh` append a row to this file in the VM's checkout
after each run. Copy new rows into a commit from a dev machine
(`ssh <vm> tail -n 5 /opt/ratchet/docs/DEPLOYS.md`).

- **result**: `PASS` = `/healthz` up, `ratchet doctor` ok, and `ratchet simulate 1 --headless`
  returned the expected BLOCK within 90 s. `FAIL` = any of those failed. `ok` = bootstrap or
  restore finished with a healthy service.
- **note**: `prev=<sha>` is the commit that was live before; `smoke=<s>` is the Simulate time;
  `edge=<caddy|tunnel>` is the edge mode after the run.
- Do one restore rehearsal before Oct 26: run `sudo deploy/vm/restore.sh <date>`
  on the VM and keep its `restore` row here.
- Rollback picks the newest `PASS`/`ok` row whose sha is not the current one.
- `RATCHET_EDGE=caddy`: every deploy and rollback also rebuilds the apex site
  (`ratchetci.dev`: product page, `/docs/`, `/bench/`) from that commit; the row covers both
  hosts. A failed docs build is logged and keeps the previous `/docs/`; it does not turn a
  row into `FAIL`. `RATCHET_EDGE=tunnel`: rows cover the demo host only; the apex is
  published by the Site workflow (`.github/workflows/site.yml`), whose runs and the Worker's
  deployment list are its record.
- `deploy.sh --edge tunnel|caddy` switches the edge mode in the same run (the row's `edge=`
  shows the new one).
- Not recorded here: the `NEBIUS_API_KEY` refresh every 4 h by `ratchet-nebius-token.timer`
  (journal only: `journalctl -u ratchet-nebius-token`). Add a row by hand (kind `ops`, result `-`) when
  you replace `/etc/ratchet/nebius-sa.json`, move DNS (`failover.sh dns`), move the tunnel
  connector to another VM, or replace `/etc/ratchet/cloudflared-token`.

Keep the table last in this file: the scripts append below it.

| time (UTC) | kind | sha | who | result | note |
| --- | --- | --- | --- | --- | --- |
