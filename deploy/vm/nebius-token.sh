#!/usr/bin/env bash
# Keep NEBIUS_API_KEY (the IAM token the Serverless Jobs runner sends) fresh. Nebius IAM
# tokens expire after 12 hours; ratchet-nebius-token.timer runs this every 4 hours and at
# boot, so a missed run or two never lets the token lapse.
#
#   sudo /opt/ratchet/deploy/vm/nebius-token.sh            # refresh now (what the timer runs)
#   sudo /opt/ratchet/deploy/vm/nebius-token.sh install    # install the pinned Nebius CLI only
#   sudo /opt/ratchet/deploy/vm/nebius-token.sh status     # CLI version, profile, token age
#
# Needs a service-account credentials file at /etc/ratchet/nebius-sa.json (root, 0600), made
# on a workstation with
#   nebius iam auth-public-key generate --service-account-id <sa-id> --output nebius-sa.json
# and copied to the VM. Without that file the refresh exits 0 and does nothing (hosts on the
# local runner). The CLI lives at /opt/ratchet/nebius/bin/nebius (official install script,
# version pinned by NEBIUS_CLI_VERSION); its profile `ratchet-sa` (endpoint api.nebius.cloud,
# parent NEBIUS_PROJECT_ID) is created from the credentials file, and recreated when the file
# changes. Its config lives in /var/lib/ratchet-ops/nebius (root, 0700).
#
# Only the NEBIUS_API_KEY= line of /etc/ratchet/env is rewritten (atomically, mode kept).
# The token is never printed or logged. ratchet-serve is restarted only when the token
# changed and RATCHET_JOB_RUNNER=nebius (Settings load once at start; ~3 s); on other
# runners the file is updated for the CLI and the next restart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

NEBIUS_CLI_VERSION="${NEBIUS_CLI_VERSION:-0.12.291}"
NEBIUS_INSTALL_SCRIPT_URL="${NEBIUS_INSTALL_SCRIPT_URL:-https://storage.eu-north1.nebius.cloud/cli/install.sh}"
NEBIUS_DIR="${RATCHET_NEBIUS_DIR:-$RATCHET_APP_DIR/nebius}"
NEBIUS_BIN="$NEBIUS_DIR/bin/nebius"
NEBIUS_HOME="${RATCHET_NEBIUS_HOME:-$RATCHET_OPS_DIR/nebius}"
NEBIUS_SA_FILE="${RATCHET_NEBIUS_SA_FILE:-/etc/ratchet/nebius-sa.json}"
NEBIUS_PROFILE="ratchet-sa"
NEBIUS_ENDPOINT="${NEBIUS_ENDPOINT:-api.nebius.cloud}"
STAMP="$RATCHET_OPS_DIR/nebius-token.stamp"

# The CLI runs with its own HOME (config + profile) and nothing from the caller's env.
nebius_cli() {
  env -i HOME="$NEBIUS_HOME" PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 \
    "$NEBIUS_BIN" "$@" </dev/null
}

cli_version() { nebius_cli version 2>/dev/null | head -n 1 || true; }

# Root runs this binary every 4 hours: refuse one that anyone else could have replaced.
check_cli_owner() {
  local path
  for path in "$NEBIUS_DIR" "$NEBIUS_DIR/bin" "$NEBIUS_BIN"; do
    [[ -e "$path" && ! -L "$path" ]] || die "$path is missing or a symlink"
    [[ "$(stat -c '%u' "$path")" == "0" ]] || die "$path is not owned by root"
    (($(stat -c '%#a' "$path") & 8#022)) && die "$path is group/world-writable"
  done
  return 0
}

install_cli() {
  local tmp
  if [[ -x "$NEBIUS_BIN" && "$(cli_version)" == "$NEBIUS_CLI_VERSION" ]]; then
    check_cli_owner
    log "nebius CLI $NEBIUS_CLI_VERSION already installed at $NEBIUS_BIN"
    return 0
  fi
  log "installing nebius CLI $NEBIUS_CLI_VERSION into $NEBIUS_DIR/bin (official script)"
  install -d -m 0755 -o root -g root "$NEBIUS_DIR" "$NEBIUS_DIR/bin"
  install -d -m 0700 -o root -g root "$NEBIUS_HOME"
  tmp="$(mktemp -d)"
  curl -fsSL --retry 3 -o "$tmp/install.sh" "$NEBIUS_INSTALL_SCRIPT_URL"
  # SHELL=/bin/sh: the script skips shell completion and rc-file edits; it checks that the
  # downloaded binary reports exactly NEBIUS_CLI_VERSION.
  env -i HOME="$NEBIUS_HOME" PATH=/usr/local/bin:/usr/bin:/bin SHELL=/bin/sh TMPDIR="$tmp" \
    NEBIUS_CLI_VERSION="$NEBIUS_CLI_VERSION" NEBIUS_INSTALL_FOLDER="$NEBIUS_DIR/bin" \
    bash "$tmp/install.sh" </dev/null >&2
  rm -rf "$tmp"
  chown root:root "$NEBIUS_BIN"
  chmod 0755 "$NEBIUS_BIN"
  check_cli_owner
  [[ "$(cli_version)" == "$NEBIUS_CLI_VERSION" ]] || die "nebius CLI reports '$(cli_version)', expected $NEBIUS_CLI_VERSION"
  # Not part of the checkout: keep it out of `git status` on the VM.
  if [[ -d "$RATCHET_APP_DIR/.git/info" ]] \
    && ! grep -qx '/nebius/' "$RATCHET_APP_DIR/.git/info/exclude" 2>/dev/null; then
    printf '/nebius/\n' >>"$RATCHET_APP_DIR/.git/info/exclude"
  fi
}

check_sa_file() {
  [[ -f "$NEBIUS_SA_FILE" && ! -L "$NEBIUS_SA_FILE" ]] || die "$NEBIUS_SA_FILE is not a regular file"
  if [[ "$(stat -c '%u:%a' "$NEBIUS_SA_FILE")" != "0:600" ]]; then
    log "WARNING: $NEBIUS_SA_FILE was $(stat -c '%U:%a' "$NEBIUS_SA_FILE"); setting root 0600 (rotate the key if others could read it)"
    chown root:root "$NEBIUS_SA_FILE"
    chmod 0600 "$NEBIUS_SA_FILE"
  fi
}

ensure_profile() {
  local project marker="$NEBIUS_HOME/$NEBIUS_PROFILE.sha256" want have=""
  project="$(env_get NEBIUS_PROJECT_ID)"
  [[ -n "$project" ]] || die "NEBIUS_PROJECT_ID is empty in $RATCHET_ENV_FILE"
  install -d -m 0700 -o root -g root "$NEBIUS_HOME"
  want="$(printf '%s %s %s' "$(sha256sum <"$NEBIUS_SA_FILE")" "$project" "$NEBIUS_ENDPOINT" | sha256sum | cut -d' ' -f1)"
  [[ -f "$marker" ]] && have="$(cat "$marker")"
  if [[ "$want" == "$have" ]] && nebius_cli profile list 2>/dev/null | grep -qw "$NEBIUS_PROFILE"; then
    return 0
  fi
  if nebius_cli profile list 2>/dev/null | grep -qw "$NEBIUS_PROFILE"; then
    log "credentials file or project changed: recreating profile $NEBIUS_PROFILE"
    nebius_cli profile delete "$NEBIUS_PROFILE" >/dev/null 2>&1 || true
  fi
  log "creating nebius CLI profile $NEBIUS_PROFILE (endpoint $NEBIUS_ENDPOINT, parent $project)"
  nebius_cli profile create --profile "$NEBIUS_PROFILE" --endpoint "$NEBIUS_ENDPOINT" \
    --service-account-file "$NEBIUS_SA_FILE" --parent-id "$project" >/dev/null
  printf '%s\n' "$want" >"$marker"
  chmod 0600 "$marker"
}

refresh() {
  local token old runner
  if [[ ! -e "$NEBIUS_SA_FILE" ]]; then
    exit 0 # local-runner host: nothing to refresh
  fi
  require_root
  check_sa_file
  [[ -f "$RATCHET_ENV_FILE" ]] || die "$RATCHET_ENV_FILE missing"
  install_cli
  ensure_profile
  token="$(nebius_cli --profile "$NEBIUS_PROFILE" iam get-access-token 2>/dev/null | tr -d '[:space:]')" \
    || die "nebius iam get-access-token failed (journalctl -u ratchet-nebius-token; run 'nebius-token.sh status')"
  ((${#token} >= 20)) || die "nebius iam get-access-token returned no token"
  [[ "$token" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || die "nebius iam get-access-token returned an unexpected value"

  take_lock 900
  old="$(env_get_file NEBIUS_API_KEY)"
  date -u +%Y-%m-%dT%H:%M:%SZ >"$STAMP"
  if [[ "$token" == "$old" ]]; then
    log "NEBIUS_API_KEY unchanged"
    return 0
  fi
  env_set NEBIUS_API_KEY "$token"
  log "NEBIUS_API_KEY refreshed in $RATCHET_ENV_FILE"
  runner="$(env_get_file RATCHET_JOB_RUNNER)"
  if [[ "$runner" == "nebius" ]]; then
    log "RATCHET_JOB_RUNNER=nebius: restarting $RATCHET_UNIT to load the new token"
    systemctl try-restart --no-block "$RATCHET_UNIT"
  else
    log "RATCHET_JOB_RUNNER=${runner:-local}: no restart (the token is read at the next start)"
  fi
}

# The env file's value, never the process environment's (a stale copy may be inherited).
env_get_file() {
  local value
  value="$(sed -n "s/^${1}=//p" "$RATCHET_ENV_FILE" | tail -n 1)"
  value="${value%\"}"
  printf '%s' "${value#\"}"
}

status() {
  require_root
  printf 'credentials: %s\n' "$([[ -f $NEBIUS_SA_FILE ]] && stat -c '%U:%a %n' "$NEBIUS_SA_FILE" || echo "absent ($NEBIUS_SA_FILE)")"
  printf 'cli:         %s (pinned %s)\n' "$([[ -x $NEBIUS_BIN ]] && cli_version || echo 'not installed')" "$NEBIUS_CLI_VERSION"
  printf 'profile:     %s\n' "$([[ -x $NEBIUS_BIN ]] && nebius_cli profile list 2>/dev/null | grep -qw "$NEBIUS_PROFILE" && echo "$NEBIUS_PROFILE" || echo missing)"
  printf 'last run:    %s\n' "$(cat "$STAMP" 2>/dev/null || echo never)"
  printf 'token set:   %s\n' "$([[ -n $(env_get_file NEBIUS_API_KEY) ]] && echo yes || echo no)"
}

case "${1:-refresh}" in
  refresh) refresh ;;
  install)
    require_root
    install_cli
    ;;
  status) status ;;
  -h | --help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)
    log "unknown command: $1"
    exit 2
    ;;
esac
