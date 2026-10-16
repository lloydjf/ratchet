#!/usr/bin/env bash
# Rotate RATCHET_ACTION_TOKEN_SECRET (run weekly by ratchet-token-rotate.timer, or by hand).
#
# The demo action token itself is HMAC(secret, UTC date)[:24] and already changes every day
# with no restart (orchestrator-api-cli.md §8); rotating the secret additionally kills every
# token issued so far. Settings load once at start, so the service is restarted (~3 s).
# Pages open across the restart re-fetch /meta for the new token.
#
#   sudo /opt/ratchet/deploy/vm/rotate-action-secret.sh [--no-restart]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
take_lock 600

[[ -f "$RATCHET_ENV_FILE" ]] || die "$RATCHET_ENV_FILE missing"
cp -p "$RATCHET_ENV_FILE" "$RATCHET_ENV_FILE.bak"
env_set RATCHET_ACTION_TOKEN_SECRET "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
log "RATCHET_ACTION_TOKEN_SECRET rotated (previous file kept at $RATCHET_ENV_FILE.bak)"

if [[ "${1:-}" != "--no-restart" ]]; then
  systemctl restart "$RATCHET_UNIT"
  wait_healthy "$RATCHET_LOCAL_URL" 90 || {
    log "unhealthy after rotation; restoring the previous env file"
    cp -p "$RATCHET_ENV_FILE.bak" "$RATCHET_ENV_FILE"
    systemctl restart "$RATCHET_UNIT"
    notify "Ratchet: action-secret rotation FAILED on $(hostname); previous secret restored"
    exit 1
  }
fi
log "done"
