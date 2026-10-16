#!/usr/bin/env bash
# The demo host's Cloudflare Tunnel connector (RATCHET_EDGE=tunnel): demo.<apex> reaches this
# VM through Cloudflare, so no port is open to the internet and Caddy listens on loopback only.
#
#   sudo edge-tunnel.sh set-token < token.txt   write /etc/ratchet/cloudflared-token (root 0600)
#                                               from stdin; nothing is echoed
#   sudo edge-tunnel.sh install                 install cloudflared (Cloudflare's apt repo,
#                                               keyring fingerprint pinned), then enable and
#                                               start ratchet-tunnel.service. Idempotent.
#   sudo edge-tunnel.sh status                  package, unit, connector readiness, token file
#   sudo edge-tunnel.sh uninstall               stop and disable the connector, remove the
#                                               package and its apt source (the token file is
#                                               kept; shred it yourself if the tunnel is gone)
#
# The tunnel is remotely managed: its ingress lives in Cloudflare (Zero Trust -> Networks ->
# Tunnels -> <tunnel> -> Public hostnames), not here. It must be exactly:
#   demo.ratchetci.dev -> HTTP -> 127.0.0.1:80        (Caddy matches the Host header)
#   catch-all          -> http_status:404
# Creating the public hostname also creates the proxied CNAME demo -> <id>.cfargotunnel.com.
# deploy/vm/dns.md has the dashboard steps and the API calls.
#
# The token is a credential for the tunnel: whoever has it can serve demo.<apex>. This script
# never prints or logs it, never puts it on a command line, and the unit reads it with
# systemd's LoadCredential=. Two VMs running the same token share the traffic (Cloudflare
# balances between connectors): keep it on one VM at a time (failover.sh help).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

KEYRING=/usr/share/keyrings/cloudflare-main.gpg
APT_LIST=/etc/apt/sources.list.d/cloudflared.list
# "CloudFlare Software Packaging 2025 <help@cloudflare.com>". If Cloudflare rotates the key,
# check the new one on https://pkg.cloudflare.com/ and pass CLOUDFLARED_KEY_FPR=<fingerprint>.
CLOUDFLARED_KEY_FPR="${CLOUDFLARED_KEY_FPR:-CC94B39C77AE7342A68B89628A682D308D4E5E73}"
TOKEN_FILE="$RATCHET_TUNNEL_TOKEN_FILE"
UNIT="$RATCHET_TUNNEL_UNIT"
UNIT_SRC="$SCRIPT_DIR/systemd/$UNIT"
READY_URL=http://127.0.0.1:20241/ready

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# The token file must exist, be non-empty, and be readable by root only. Fixes the mode and
# owner when they are wrong; never reads the content into a variable.
check_token_file() {
  if [[ ! -s "$TOKEN_FILE" ]]; then
    log "no tunnel token at $TOKEN_FILE"
    log "write it with: sudo $0 set-token < token.txt   (Cloudflare -> Zero Trust -> Networks -> Tunnels -> <tunnel> -> install connector; the token is the long argument after 'install')"
    return 1
  fi
  if [[ "$(stat -c '%U:%G %a' "$TOKEN_FILE")" != "root:root 600" ]]; then
    log "fixing owner/mode of $TOKEN_FILE (root:root 0600)"
    chown root:root "$TOKEN_FILE"
    chmod 0600 "$TOKEN_FILE"
  fi
}

cmd_set_token() {
  local tmp
  [[ ! -t 0 ]] || die "pipe the token on stdin: sudo $0 set-token < token.txt (or: read -rs T; printf %s \"\$T\" | sudo $0 set-token)"
  install -d -m 0700 -o root -g root "$(dirname "$TOKEN_FILE")"
  tmp="$(mktemp "${TOKEN_FILE}.XXXXXX")"
  chmod 0600 "$tmp"
  # Whitespace (a trailing newline, a pasted line break) is dropped; the bytes never reach a
  # variable, an argument or the log.
  head -c 8192 | tr -d '[:space:]' >"$tmp"
  if [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    die "empty token on stdin; nothing written"
  fi
  mv -f "$tmp" "$TOKEN_FILE"
  chown root:root "$TOKEN_FILE"
  log "wrote $TOKEN_FILE (root 0600, $(wc -c <"$TOKEN_FILE") bytes)"
  if systemctl is-enabled --quiet "$UNIT" 2>/dev/null; then
    log "restarting $UNIT with the new token"
    systemctl restart "$UNIT"
  fi
}

install_package() {
  local fpr
  export DEBIAN_FRONTEND=noninteractive
  if [[ ! -s "$KEYRING" ]]; then
    log "fetching Cloudflare's package signing key"
    command -v gpg >/dev/null || apt-get install -y -q --no-install-recommends gnupg
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o "${KEYRING}.new"
    fpr="$(gpg --show-keys --with-colons "${KEYRING}.new" 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')"
    if [[ "$fpr" != "$CLOUDFLARED_KEY_FPR" ]]; then
      rm -f "${KEYRING}.new"
      die "Cloudflare signing key fingerprint is ${fpr:-unreadable}, expected $CLOUDFLARED_KEY_FPR; not installing"
    fi
    chmod 0644 "${KEYRING}.new"
    mv -f "${KEYRING}.new" "$KEYRING"
  fi
  echo "deb [signed-by=${KEYRING}] https://pkg.cloudflare.com/cloudflared any main" >"$APT_LIST"
  if ! command -v cloudflared >/dev/null; then
    log "installing cloudflared (pkg.cloudflare.com)"
    apt-get update -q
    apt-get install -y -q --no-install-recommends cloudflared
  fi
  log "$(cloudflared --version 2>&1 | head -n 1)"
}

cmd_install() {
  install_package
  install -m 0644 "$UNIT_SRC" "/etc/systemd/system/$UNIT"
  systemctl daemon-reload
  if ! check_token_file; then
    log "cloudflared is installed; $UNIT not started until the token is in place"
    exit 3
  fi
  systemctl enable --quiet "$UNIT"
  if systemctl is-active --quiet "$UNIT"; then
    systemctl try-restart "$UNIT"
  else
    systemctl start "$UNIT"
  fi
  wait_ready 30 || die "connector not ready; journalctl -u $UNIT -n 50 (wrong or revoked token?)"
  log "tunnel connector up; ingress is remote: demo host -> http://127.0.0.1:80 (deploy/vm/dns.md)"
}

wait_ready() {
  local deadline=$((SECONDS + ${1:-30}))
  while ((SECONDS < deadline)); do
    if curl -fsS -o /dev/null --max-time 3 "$READY_URL"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

cmd_status() {
  local state ready mode
  printf 'edge mode:  %s\n' "$(env_get RATCHET_EDGE caddy)"
  printf 'cloudflared: %s\n' "$(cloudflared --version 2>/dev/null | head -n 1 || echo 'not installed')"
  state="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
  printf 'unit:       %s (%s, %s)\n' "$UNIT" "${state:-unknown}" \
    "$(systemctl is-enabled "$UNIT" 2>/dev/null || echo 'not installed')"
  ready="$(curl -fsS --max-time 3 -o /dev/null -w '%{http_code}' "$READY_URL" 2>/dev/null || echo down)"
  printf 'ready:      %s (%s)\n' "$ready" "$READY_URL"
  if [[ -e "$TOKEN_FILE" ]]; then
    mode="$(stat -c '%U:%G %a' "$TOKEN_FILE")"
    printf 'token file: %s (%s, %s bytes)\n' "$TOKEN_FILE" "$mode" "$(wc -c <"$TOKEN_FILE")"
  else
    printf 'token file: %s (missing)\n' "$TOKEN_FILE"
  fi
  printf 'caddy:      %s\n' "$(systemctl is-active caddy 2>/dev/null || true)"
}

cmd_uninstall() {
  if systemctl list-unit-files "$UNIT" >/dev/null 2>&1; then
    log "stopping and disabling $UNIT"
    systemctl disable --now --quiet "$UNIT" 2>/dev/null || true
  fi
  rm -f "/etc/systemd/system/$UNIT"
  systemctl daemon-reload
  if dpkg -s cloudflared >/dev/null 2>&1; then
    log "removing cloudflared"
    DEBIAN_FRONTEND=noninteractive apt-get remove -y -q cloudflared
  fi
  rm -f "$APT_LIST" "$KEYRING"
  if [[ -e "$TOKEN_FILE" ]]; then
    log "kept $TOKEN_FILE; if the tunnel is retired: sudo shred -u $TOKEN_FILE"
  fi
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    status) cmd_status ;;
    install | set-token | uninstall)
      require_root
      take_lock 300
      case "$cmd" in
        install) cmd_install ;;
        set-token) cmd_set_token ;;
        uninstall) cmd_uninstall ;;
      esac
      ;;
    -h | --help | "") usage 0 ;;
    *) usage 2 ;;
  esac
}

main "$@"
