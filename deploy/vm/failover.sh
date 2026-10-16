#!/usr/bin/env bash
# Failover for the public demo URL ("demo down"). Three levels, cheapest first. The first two
# act on the demo host only (RATCHET_DEMO_ADDRESS) and work in both edge modes; the apex site
# is static (caddy mode: files on this VM; tunnel mode: Cloudflare) and keeps serving.
#
#   sudo failover.sh fallback on|off      app broken, VM and Caddy fine: every request gets
#                                         the static page (video + note); /healthz stays real
#   sudo failover.sh proxy-to <host:port> app broken here, a standby VM is serving: this
#                                         VM's Caddy forwards to the standby's ratchet-serve
#   sudo failover.sh proxy-local          undo proxy-to (back to 127.0.0.1:8080)
#   failover.sh [--edge caddy|tunnel] dns <standby-ip>
#                                         this VM is gone. caddy mode: point DNS (apex + demo)
#                                         at the standby; runs RATCHET_DNS_HOOK per host if
#                                         set, else prints the steps. tunnel mode: no DNS
#                                         change; prints the standby steps (same token)
#   failover.sh status
#
# --edge overrides RATCHET_EDGE from /etc/ratchet/env (e.g. when run from a laptop).
#
# Standby VM (prepare before Oct 29; manual):
#   1. Nebius console -> Compute -> the demo VM's boot disk -> Create snapshot (after a good
#      deploy). Create a second VM from it in the same project/network, same size.
#   2. On the standby: sudo /opt/ratchet/deploy/vm/restore.sh <latest-date> (or copy the
#      tarball from object storage), then sudo /opt/ratchet/deploy/vm/deploy.sh <sha>.
#   3. For proxy-to, set RATCHET_DASHBOARD_HOST to the standby's *private* IP in its
#      /etc/ratchet/env and allow 8080 from the primary's private IP only (security group).
#   4. caddy mode, for dns: the standby's Caddy obtains its own certificates once the A
#      records resolve to it (HTTP-01 on port 80). Keep the records' TTL at 60 s during the
#      judging window (deploy/vm/dns.md).
#   5. tunnel mode: the snapshot carries /etc/ratchet/cloudflared-token and an enabled
#      ratchet-tunnel.service, and two connectors with one token share the traffic. Right
#      after creating the standby: sudo systemctl disable --now ratchet-tunnel. To take over
#      (no DNS change: the tunnel's hostname follows whichever VM runs the connector):
#      sudo /opt/ratchet/deploy/vm/edge-tunnel.sh install on the standby, then stop the
#      connector on the old VM if it is still up (sudo systemctl disable --now ratchet-tunnel).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

MARKER=/etc/caddy/ratchet-fallback.enabled
DROPIN="$RATCHET_CADDY_DROPIN"
EDGE=""

usage() {
  sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
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

url_host() {
  local host="${1#*://}"
  host="${host%%/*}"
  printf '%s' "${host%%:*}"
}

# tunnel mode: the public hostname is a CNAME to the tunnel, not to an IP, so a standby takes
# over by running the connector with the same token. Nothing to change in DNS.
cmd_tunnel_takeover() {
  local ip="$1" public
  public="$(env_get RATCHET_PUBLIC_URL https://demo.ratchetci.dev)"
  cat >&2 <<EOF
RATCHET_EDGE=tunnel: no DNS change. ${public} is a CNAME to the tunnel; it serves from
whichever VM runs the connector. The apex (ratchetci.dev, /docs/, /bench/) is on Cloudflare
and is not affected.
  1. On the standby (${ip}): it must be healthy first:  curl -fsS http://127.0.0.1:8080/healthz
     It needs /etc/ratchet/cloudflared-token (same token; from the snapshot, or
     sudo /opt/ratchet/deploy/vm/edge-tunnel.sh set-token < token.txt).
  2. On the standby: sudo /opt/ratchet/deploy/vm/edge-tunnel.sh install
  3. If the old VM is still reachable: sudo systemctl disable --now ratchet-tunnel there
     (two connectors with one token share the traffic). If it is gone, its connections drop
     on their own.
  4. Verify: curl -fsS ${public}/healthz; sudo /opt/ratchet/deploy/vm/edge-tunnel.sh status
  5. Tell the team; record the switch in docs/DEPLOYS.md.
EOF
}

cmd_dns() {
  local ip="${1:?usage: failover.sh dns <standby-ip>}" hook host hosts=()
  if [[ "$EDGE" == "tunnel" ]]; then
    cmd_tunnel_takeover "$ip"
    return 0
  fi
  for host in "$(url_host "$(env_get RATCHET_SITE_URL)")" "$(url_host "$(env_get RATCHET_PUBLIC_URL)")"; do
    [[ -n "$host" ]] && hosts+=("$host")
  done
  ((${#hosts[@]})) || hosts=("<host>")
  hook="$(env_get RATCHET_DNS_HOOK)"
  if [[ -n "$hook" && -x "$hook" ]]; then
    for host in "${hosts[@]}"; do
      log "running RATCHET_DNS_HOOK $hook $host $ip"
      "$hook" "$host" "$ip"
    done
  else
    cat >&2 <<EOF
Manual DNS failover for ${hosts[*]} -> ${ip} (deploy/vm/dns.md has the Cloudflare API calls):
  1. DNS provider: set the A record of each of ${hosts[*]} to ${ip} (TTL 60), DNS only (no
     proxy). Remove any old AAAA. www stays a CNAME of the apex.
  2. Wait for it: dig +short ${hosts[0]} @1.1.1.1   (should print ${ip}; same for each host)
  3. On the standby: sudo systemctl restart caddy; watch journalctl -u caddy for "certificate obtained".
  4. Verify: curl -fsS https://${hosts[-1]}/healthz and curl -fsSI https://${hosts[0]}/
  5. Tell the team; record the switch in docs/DEPLOYS.md.
Automate with RATCHET_DNS_HOOK=/usr/local/bin/<script> in /etc/ratchet/env (called once per host as: <script> <host> <ip>).
EOF
  fi
}

cmd_status() {
  local upstream
  upstream="$(sed -n 's/^Environment=RATCHET_UPSTREAM_ADDR=//p' "$DROPIN" 2>/dev/null || true)"
  printf 'edge: %s\nfallback: %s\nupstream: %s\n' "$EDGE" \
    "$([[ -e $MARKER ]] && echo on || echo off)" "${upstream:-unknown}"
  if [[ "$EDGE" == "tunnel" ]]; then
    printf 'tunnel connector: %s\n' "$(systemctl is-active "$RATCHET_TUNNEL_UNIT" 2>/dev/null || true)"
  fi
  printf 'local health: '
  curl -fsS --max-time 3 -o /dev/null -w '%{http_code}\n' "$RATCHET_LOCAL_URL/healthz" || echo down
}

main() {
  if [[ "${1:-}" == "--edge" ]]; then
    EDGE="${2:?--edge needs caddy or tunnel}"
    shift 2
  fi
  [[ -n "$EDGE" ]] || EDGE="$(edge_mode)"
  [[ "$EDGE" == "caddy" || "$EDGE" == "tunnel" ]] || die "--edge must be caddy or tunnel"
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
