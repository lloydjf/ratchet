#!/usr/bin/env bash
# Shared helpers for deploy/vm/*.sh. Sourced, never executed. Layout on the VM:
#   /opt/ratchet                 git checkout (owner ratchet), .venv, dashboard/dist
#   /srv/ratchet/{data,skills}   runtime state (the only writable path of the service)
#   /srv/ratchet/tmp             TMPDIR (visible to the Docker daemon, unlike PrivateTmp)
#   /etc/ratchet/env             secrets + settings, root:root 0600, read by systemd
#   /var/lib/ratchet-ops         deploy ledger (deploys.tsv), failover state
#   /var/backups/ratchet         nightly tarballs (14-day retention)
# shellcheck shell=bash

RATCHET_APP_DIR="${RATCHET_APP_DIR:-/opt/ratchet}"
RATCHET_SRV_DIR="${RATCHET_SRV_DIR:-/srv/ratchet}"
RATCHET_ENV_FILE="${RATCHET_ENV_FILE:-/etc/ratchet/env}"
RATCHET_OPS_DIR="${RATCHET_OPS_DIR:-/var/lib/ratchet-ops}"
RATCHET_BACKUP_DIR="${RATCHET_BACKUP_DIR:-/var/backups/ratchet}"
RATCHET_FALLBACK_DIR="${RATCHET_FALLBACK_DIR:-/var/www/ratchet-fallback}"
RATCHET_USER="${RATCHET_USER:-ratchet}"
RATCHET_UNIT="${RATCHET_UNIT:-ratchet-serve.service}"
RATCHET_LEDGER="$RATCHET_OPS_DIR/deploys.tsv"
RATCHET_LOCAL_URL="${RATCHET_LOCAL_URL:-http://127.0.0.1:8080}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/uv/python}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/var/cache/ratchet/uv}"
RATCHET_NPM_CACHE="${RATCHET_NPM_CACHE:-/var/cache/ratchet/npm}"
export npm_config_cache="$RATCHET_NPM_CACHE"

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${0##*/}" "$*" >&2; }
die() {
  log "ERROR: $*"
  exit 1
}

require_root() { [[ $EUID -eq 0 ]] || die "run as root: sudo $0"; }

# Serialize every mutating ops script (deploy, rollback, restore, failover, rotate).
# A child ops script (rollback -> deploy) inherits the parent's lock instead of waiting on it.
take_lock() {
  [[ "${RATCHET_OPS_LOCKED:-}" == "1" ]] && return 0
  mkdir -p "$RATCHET_OPS_DIR"
  exec 9>"$RATCHET_OPS_DIR/ops.lock"
  flock -w "${1:-600}" 9 || die "another ops script holds $RATCHET_OPS_DIR/ops.lock"
  export RATCHET_OPS_LOCKED=1
}

# Read KEY from the env file without `source` (values such as JSON caps are not shell-safe).
# The process environment wins, so systemd units (EnvironmentFile=) and overrides work.
env_get() {
  local key="$1" default="${2:-}" value
  if [[ -n "${!key:-}" ]]; then
    printf '%s' "${!key}"
    return
  fi
  if [[ -r "$RATCHET_ENV_FILE" ]]; then
    value="$(sed -n "s/^${key}=//p" "$RATCHET_ENV_FILE" | tail -n 1)"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    if [[ -n "$value" ]]; then
      printf '%s' "$value"
      return
    fi
  fi
  printf '%s' "$default"
}

# Set KEY=VALUE in the env file atomically (0600, root), keeping every other line.
env_set() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp "${RATCHET_ENV_FILE}.XXXXXX")"
  chmod 600 "$tmp"
  if [[ -f "$RATCHET_ENV_FILE" ]]; then
    grep -v "^${key}=" "$RATCHET_ENV_FILE" >"$tmp" || true
  fi
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  mv -f "$tmp" "$RATCHET_ENV_FILE"
}

as_ratchet() {
  runuser -u "$RATCHET_USER" -- env \
    HOME="$RATCHET_SRV_DIR" \
    PATH="/usr/local/bin:/usr/bin:/bin" \
    UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
    UV_CACHE_DIR="$UV_CACHE_DIR" \
    npm_config_cache="$RATCHET_NPM_CACHE" \
    "$@"
}

# A script that checks out another commit rewrites its own file; bash reads scripts lazily,
# so mutating scripts first re-exec from a private copy of deploy/vm.
reexec_from_copy() {
  local script="$1"
  shift
  if [[ -n "${RATCHET_OPS_REEXEC:-}" ]]; then
    return 0
  fi
  local copy
  copy="$(mktemp -d /tmp/ratchet-ops.XXXXXX)"
  cp -a "$(dirname "$script")/." "$copy/"
  RATCHET_OPS_REEXEC="$copy" exec bash "$copy/$(basename "$script")" "$@"
}

# Run the ratchet CLI exactly as the service would: same user, env file, cwd and sandboxing
# of the filesystem, but as a transient unit so the root-only env file is read by systemd.
# RATCHET_CLI_TIMEOUT=<s> bounds the run (RuntimeMaxSec); the exit status is the CLI's.
ratchet_cli() {
  systemd-run --quiet --wait --pipe --collect \
    --property=RuntimeMaxSec="${RATCHET_CLI_TIMEOUT:-infinity}" \
    --uid="$RATCHET_USER" --gid="$RATCHET_USER" \
    --property=EnvironmentFile="$RATCHET_ENV_FILE" \
    --property=WorkingDirectory="$RATCHET_APP_DIR" \
    --property=ProtectSystem=strict \
    --property=ReadWritePaths="$RATCHET_SRV_DIR" \
    --property=NoNewPrivileges=yes \
    --setenv=HOME="$RATCHET_SRV_DIR" \
    --setenv=TMPDIR="$RATCHET_SRV_DIR/tmp" \
    --setenv=PYTHONUNBUFFERED=1 \
    "$RATCHET_APP_DIR/.venv/bin/ratchet" "$@"
}

# Wait until GET <url>/healthz answers 200 (default 60 s).
wait_healthy() {
  local url="${1:-$RATCHET_LOCAL_URL}" timeout="${2:-60}" deadline
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    if curl -fsS -o /dev/null --max-time 3 "$url/healthz"; then
      log "healthy: $url/healthz"
      return 0
    fi
    sleep 2
  done
  log "not healthy after ${timeout}s: $url/healthz"
  return 1
}

# git always runs as the checkout's owner (root would trip git's safe.directory check).
app_git() { runuser -u "$RATCHET_USER" -- env HOME="$RATCHET_SRV_DIR" git -C "$RATCHET_APP_DIR" "$@"; }

current_sha() { app_git rev-parse HEAD 2>/dev/null || echo unknown; }

# Ledger row: utc_time  kind  sha  who  result  note   (tab-separated; rollback reads it)
ledger_append() {
  local kind="$1" sha="$2" result="$3" note="${4:-}" who
  who="${SUDO_USER:-$(id -un)}"
  mkdir -p "$RATCHET_OPS_DIR"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$kind" "$sha" "$who" \
    "$result" "$note" >>"$RATCHET_LEDGER"
}

# Re-render docs/DEPLOYS.md in the checkout: committed rows + ledger rows not yet in it.
# The checkout copy is discarded before every `git checkout`, so it never blocks one; the
# ledger is the source of truth. Commit new rows from a dev machine (docs/DEPLOYS.md).
deploys_md_sync() {
  local md="$RATCHET_APP_DIR/docs/DEPLOYS.md" time kind sha who result note row
  [[ -f "$md" && -f "$RATCHET_LEDGER" ]] || return 0
  while IFS=$'\t' read -r time kind sha who result note; do
    row="| ${time} | ${kind} | \`${sha:0:12}\` | ${who} | ${result} | ${note} |"
    grep -qF "| ${time} | ${kind} |" "$md" || printf '%s\n' "$row" >>"$md"
  done <"$RATCHET_LEDGER"
  chown "$RATCHET_USER:$RATCHET_USER" "$md"
}

deploys_md_reset() {
  app_git checkout --quiet -- docs/DEPLOYS.md 2>/dev/null || true
}

notify() {
  local text="$1" hook
  hook="$(env_get RATCHET_NOTIFY_WEBHOOK)"
  [[ -n "$hook" ]] || return 0
  python3 - "$hook" "$text" <<'PY' || log "webhook notification failed"
import json, sys, urllib.request
hook, text = sys.argv[1], sys.argv[2]
body = json.dumps({"text": text}).encode()
req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=5)
PY
}

# Install the static fallback page with the video embed and repo link filled in.
render_fallback() {
  local src="$RATCHET_APP_DIR/deploy/vm/static-fallback/index.html"
  mkdir -p "$RATCHET_FALLBACK_DIR"
  python3 - "$src" "$RATCHET_FALLBACK_DIR/index.html" "$(env_get RATCHET_VIDEO_URL)" \
    "$(env_get RATCHET_REPO_URL https://github.com/lloydjf/ratchet)" <<'PY'
import html, re, sys
src, dst, video, repo = sys.argv[1:5]
page = open(src, encoding="utf-8").read()
match = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([\w-]{6,})", video)
if match:
    embed = (
        f'<iframe src="https://www.youtube-nocookie.com/embed/{match.group(1)}" '
        'title="Ratchet demo video" loading="lazy" referrerpolicy="strict-origin" '
        'allow="encrypted-media; picture-in-picture" allowfullscreen></iframe>'
    )
elif video:
    embed = (
        '<div class="placeholder"><div><strong>Watch the demo</strong>'
        f'<a href="{html.escape(video)}">{html.escape(video)}</a></div></div>'
    )
else:
    embed = (
        '<div class="placeholder"><div><strong>Demo video</strong>'
        "The three-minute walkthrough will be linked here.</div></div>"
    )
page = page.replace("__RATCHET_VIDEO_EMBED__", embed)
page = page.replace("__RATCHET_REPO_URL__", html.escape(repo))
open(dst, "w", encoding="utf-8").write(page)
PY
  chmod 0644 "$RATCHET_FALLBACK_DIR/index.html"
}
