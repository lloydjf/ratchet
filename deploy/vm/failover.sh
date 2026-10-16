#!/usr/bin/env bash
# Failover for the public demo URL ("demo down"). Three levels, cheapest first:
#
#   sudo failover.sh fallback on|off      app broken, VM and Caddy fine: every request gets
#                                         the static page (video + note); /healthz stays real
#   sudo failover.sh proxy-to <host:port> app broken here, a standby VM is serving: this
#                                         VM's Caddy (which holds the cert and the DNS name)
#                                         forwards to the standby's ratchet-serve
#   sudo failover.sh proxy-local          undo proxy-to (back to 127.0.0.1:8080)
#   failover.sh dns <standby-ip>          this VM is gone: point DNS at the standby; runs
#                                         RATCHET_DNS_HOOK if set, else prints the steps
#   failover.sh status
#
# Standby VM (prepare before Oct 29; manual):
#   1. Nebius console -> Compute -> the demo VM's boot disk -> Create snapshot (after a good
#      deploy). Create a second VM from it in the same project/network, same size.
#   2. On the standby: sudo /opt/ratchet/deploy/vm/restore.sh <latest-date> (or copy the
#      tarball from object storage), then sudo /opt/ratchet/deploy/vm/deploy.sh <sha>.
#   3. For proxy-to, set RATCHET_DASHBOARD_HOST to the standby's *private* IP in its
#      /etc/ratchet/env and allow 8080 from the primary's private IP only (security group).
#   4. For dns, the standby's Caddy obtains its own certificate once the A record resolves
#      to it (HTTP-01 on port 80). Keep the record's TTL at 60 s during the judging window.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

MARKER=/etc/caddy/ratchet-fallback.enabled
DROPIN=/etc/systemd/system/caddy.service.d/ratchet.conf

usage() {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

set_upstream() {
  local upstream="$1"
  [[ -f "$DROPIN" ]] || die "$DROPIN missing; run bootstrap.sh first"
  sed -i "s|^Environment=RATCHET_UPSTREAM_ADDR=.*|Environment=RATCHET_UPSTREAM_ADDR=${upstream}|" "$DROPIN"
  grep -q '^Environment=RATCHET_UPSTREAM_ADDR=' "$DROPIN" \
    || printf 'Environment=RATCHET_UPSTREAM_ADDR=%s\n' "$upstream" >>"$DROPIN"
  systemctl daemon-reload
  systemctl restart caddy
  printf '%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$upstream" >>"$RATCHET_OPS_DIR/failover.log"
}

cmd_fallback() {
  case "${1:-}" in
    on)
      render_fallback
      touch "$MARKER"
      log "static fallback ON for every request (no Caddy reload needed)"
      notify "Ratchet: static fallback ON on $(hostname)"
      ;;
    off)
      rm -f "$MARKER"
      log "static fallback OFF"
      notify "Ratchet: static fallback OFF on $(hostname)"
      ;;
    *) usage 2 ;;
  esac
}

cmd_proxy_to() {
  local target="${1:?usage: failover.sh proxy-to <host:port>}"
  log "checking standby http://$target/healthz"
  curl -fsS --max-time 5 -o /dev/null "http://$target/healthz" || die "standby $target is not healthy"
  set_upstream "$target"
  log "Caddy now forwards to $target"
  notify "Ratchet: $(hostname) Caddy now forwards to standby $target"
}

cmd_proxy_local() {
  set_upstream "127.0.0.1:8080"
  log "Caddy forwards to the local ratchet-serve again"
}

cmd_dns() {
  local ip="${1:?usage: failover.sh dns <standby-ip>}" public host hook
  public="$(env_get RATCHET_PUBLIC_URL)"
  host="${public#*://}"
  host="${host%%/*}"
  host="${host%%:*}"
  hook="$(env_get RATCHET_DNS_HOOK)"
  if [[ -n "$hook" && -x "$hook" ]]; then
    log "running RATCHET_DNS_HOOK $hook $host $ip"
    "$hook" "$host" "$ip"
  else
    cat >&2 <<EOF
Manual DNS failover for ${host:-<host>} -> ${ip}:
  1. DNS provider: set the A record for ${host:-<host>} to ${ip} (TTL 60). Remove the old AAAA if any.
  2. Wait for it: dig +short ${host:-<host>} @1.1.1.1   (should print ${ip})
  3. On the standby: sudo systemctl restart caddy; watch journalctl -u caddy for "certificate obtained".
  4. Verify: curl -fsS https://${host:-<host>}/healthz
  5. Tell the team; record the switch in docs/DEPLOYS.md.
Automate with RATCHET_DNS_HOOK=/usr/local/bin/<script> in /etc/ratchet/env (called as: <script> <host> <ip>).
EOF
  fi
}

cmd_status() {
  local upstream
  upstream="$(sed -n 's/^Environment=RATCHET_UPSTREAM_ADDR=//p' "$DROPIN" 2>/dev/null || true)"
  printf 'fallback: %s\nupstream: %s\nlocal health: ' "$([[ -e $MARKER ]] && echo on || echo off)" "${upstream:-unknown}"
  curl -fsS --max-time 3 -o /dev/null -w '%{http_code}\n' "$RATCHET_LOCAL_URL/healthz" || echo down
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    status) cmd_status ;;
    dns) cmd_dns "$@" ;;
    fallback | proxy-to | proxy-local)
      require_root
      take_lock 120
      case "$cmd" in
        fallback) cmd_fallback "$@" ;;
        proxy-to) cmd_proxy_to "$@" ;;
        proxy-local) cmd_proxy_local ;;
      esac
      ;;
    -h | --help | "") usage 0 ;;
    *) usage 2 ;;
  esac
}

main "$@"
