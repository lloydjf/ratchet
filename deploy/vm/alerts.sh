#!/usr/bin/env bash
# In-VM alert checks; run every 2 min by ratchet-alerts.timer.
#
#   demo down         local /healthz fails 2 runs in a row (~2-4 min)  -> restart.sh, failover.sh
#   disk              the /srv/ratchet filesystem > 80 % used           -> ratchet purge --before
#   spend surge       ratchet_model_usd_total rises > $5 within 1 h     -> lower purpose caps
#                     (proxy registry, $RATCHET_PROXY_METRICS_URL, :8081)
#   simulate failing  >= 2 of the last 5 Simulate runs failed           -> replayed mode
#   jobs stalled      a replay batch without a heartbeat for 120 s      -> check the runner
#                     (dashboard registry, $RATCHET_LOCAL_URL/metrics, :8080)
#
# The checks live in alerts_check.py (stdlib only; tests/unit/test_alerts_check.py). Which
# metric is on which registry: docs/RUNBOOK.md "Metrics".
# Alerts go to RATCHET_NOTIFY_WEBHOOK ({"text": ...}; Slack-compatible) and the journal, at
# most once per 30 min per kind, plus a recovery message. State: /var/lib/ratchet-ops/alerts.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

RATCHET_PROXY_METRICS_URL="${RATCHET_PROXY_METRICS_URL:-http://127.0.0.1:8081}"

mkdir -p "$RATCHET_OPS_DIR"
messages="$(
  python3 -I "$SCRIPT_DIR/alerts_check.py" "$RATCHET_LOCAL_URL" "$RATCHET_PROXY_METRICS_URL" \
    "$RATCHET_SRV_DIR" "$RATCHET_OPS_DIR/alerts.json"
)"

if [[ -n "$messages" ]]; then
  while IFS= read -r line; do
    log "$line"
    notify "Ratchet $(hostname): $line"
  done <<<"$messages"
fi
