#!/usr/bin/env bash
# First response to "demo down" (incident playbook in docs/RUNBOOK.md).
#
#   sudo /opt/ratchet/deploy/vm/restart.sh          # restart ratchet-serve, reload Caddy
#   sudo /opt/ratchet/deploy/vm/restart.sh --all    # also restart Caddy and Docker
#
# RATCHET_EDGE=tunnel: also restarts the tunnel connector (ratchet-tunnel.service).
# Waits for /healthz locally and on RATCHET_PUBLIC_URL, checks the certificate's expiry (in
# tunnel mode that is Cloudflare's edge certificate), and exits non-zero with the next step
# (failover.sh) when the URL is still down.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

ALL=0
[[ "${1:-}" == "--all" ]] && ALL=1

require_root
take_lock 120

cert_days_left() {
  local host="$1"
  python3 - "$host" <<'PY' || true
import socket, ssl, sys, time
host = sys.argv[1]
ctx = ssl.create_default_context()
with socket.create_connection((host, 443), timeout=5) as sock:
    with ctx.wrap_socket(sock, server_hostname=host) as tls:
        expires = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
print(int((expires - time.time()) // 86400))
PY
}

main() {
  local public host days url edge
  public="$(env_get RATCHET_PUBLIC_URL)"
  edge="$(edge_mode)"
  if ((ALL)); then
    log "restarting docker"
    systemctl restart docker
  fi
  log "restarting $RATCHET_UNIT"
  systemctl restart "$RATCHET_UNIT"
  caddy_log_dir
  if ((ALL)); then
    log "restarting caddy"
    systemctl restart caddy
  else
    log "reloading caddy"
    systemctl reload caddy || systemctl restart caddy
  fi
  if [[ "$edge" == "tunnel" ]]; then
    log "restarting $RATCHET_TUNNEL_UNIT"
    systemctl restart "$RATCHET_TUNNEL_UNIT" \
      || log "WARNING: $RATCHET_TUNNEL_UNIT did not start: sudo deploy/vm/edge-tunnel.sh status"
  fi
  wait_healthy "$RATCHET_LOCAL_URL" 90 || {
    journalctl -u "$RATCHET_UNIT" -n 50 --no-pager >&2 || true
    die "local /healthz still failing. Next: deploy/vm/failover.sh fallback on (static page), then rollback.sh"
  }
  # Both certificates: the demo (RATCHET_PUBLIC_URL) and the apex site (RATCHET_SITE_URL).
  for url in "$public" "$(env_get RATCHET_SITE_URL)"; do
    [[ "$url" == https://* ]] || continue
    host="${url#https://}"
    host="${host%%/*}"
    host="${host%%:*}"
    days="$(cert_days_left "$host")"
    if [[ -n "$days" ]]; then
      log "TLS certificate for $host expires in ${days} days"
    else
      log "WARNING: could not read the TLS certificate for $host"
    fi
  done
  if [[ -n "$public" ]] && ! wait_healthy "$public" 30; then
    if [[ "$edge" == "tunnel" ]]; then
      die "public URL still down ($public) though the app is healthy: check deploy/vm/edge-tunnel.sh status, journalctl -u $RATCHET_TUNNEL_UNIT, the tunnel's public hostname (deploy/vm/dns.md); else deploy/vm/failover.sh dns <standby-ip> (prints the tunnel takeover)"
    fi
    die "public URL still down ($public) though the app is healthy: check DNS, Caddy (journalctl -u caddy), the VM's security group; else deploy/vm/failover.sh dns <standby-ip>"
  fi
  log "ok"
}

main
