#!/usr/bin/env bash
# Shared helpers for deploy/vm/*.sh. Sourced, never executed. Layout on the VM:
#   /opt/ratchet                 git checkout (owner ratchet), .venv, dashboard/dist
#   /srv/ratchet/{data,skills}   runtime state (the only writable path of the service)
#   /srv/ratchet/tmp             TMPDIR (visible to the Docker daemon, unlike PrivateTmp)
#   /etc/ratchet/env             secrets + settings, root:root 0600, read by systemd
#   /var/lib/ratchet-ops         deploy ledger (deploys.tsv), failover state
#   /var/backups/ratchet         nightly tarballs (14-day retention)
#   /var/www/ratchet-site        the apex site (symlink to a release under ratchet-site.d/)
#   /etc/caddy/ratchet.d         generated Caddy fragments (alt-domain redirects, docs CSP)
#   /etc/ratchet/cloudflared-token  Cloudflare Tunnel token, root 0600 (RATCHET_EDGE=tunnel)
# shellcheck shell=bash

RATCHET_APP_DIR="${RATCHET_APP_DIR:-/opt/ratchet}"
RATCHET_SRV_DIR="${RATCHET_SRV_DIR:-/srv/ratchet}"
RATCHET_ENV_FILE="${RATCHET_ENV_FILE:-/etc/ratchet/env}"
RATCHET_OPS_DIR="${RATCHET_OPS_DIR:-/var/lib/ratchet-ops}"
RATCHET_BACKUP_DIR="${RATCHET_BACKUP_DIR:-/var/backups/ratchet}"
RATCHET_FALLBACK_DIR="${RATCHET_FALLBACK_DIR:-/var/www/ratchet-fallback}"
RATCHET_SITE_DIR="${RATCHET_SITE_DIR:-/var/www/ratchet-site}"
RATCHET_CADDY_EXTRA_DIR="${RATCHET_CADDY_EXTRA_DIR:-/etc/caddy/ratchet.d}"
RATCHET_CADDY_DROPIN="${RATCHET_CADDY_DROPIN:-/etc/systemd/system/caddy.service.d/ratchet.conf}"
RATCHET_TUNNEL_UNIT="${RATCHET_TUNNEL_UNIT:-ratchet-tunnel.service}"
RATCHET_TUNNEL_TOKEN_FILE="${RATCHET_TUNNEL_TOKEN_FILE:-/etc/ratchet/cloudflared-token}"
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
# Called with or without a wait (seconds); the default is 10 minutes.
# shellcheck disable=SC2120
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

# Set KEY=VALUE in the env file atomically, keeping every other line where it is and the
# file's mode and owner (0600 root for /etc/ratchet/env). The value is written as given, so
# a secret passed here never reaches a command line or a log.
env_set() {
  local key="$1" value="$2" tmp line found=0
  tmp="$(mktemp "${RATCHET_ENV_FILE}.XXXXXX")"
  chmod 600 "$tmp"
  if [[ -f "$RATCHET_ENV_FILE" ]]; then
    chmod --reference="$RATCHET_ENV_FILE" "$tmp"
    chown --reference="$RATCHET_ENV_FILE" "$tmp"
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" == "${key}="* ]]; then
        ((found)) || printf '%s=%s\n' "$key" "$value"
        found=1
      else
        printf '%s\n' "$line"
      fi
    done <"$RATCHET_ENV_FILE" >"$tmp"
  fi
  ((found)) || printf '%s=%s\n' "$key" "$value" >>"$tmp"
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

# --- Caddy and the apex site --------------------------------------------------------------------

# Caddy site address for a URL: the bare host (automatic HTTPS) for https://<host>, else
# ":<port>" (plain HTTP, no certificate).
site_address() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit
url = urlsplit(sys.argv[1])
host = url.hostname or ""
if url.scheme == "https" and host:
    print(host if not url.port else f"{host}:{url.port}")
else:
    print(f":{url.port or 80}")  # plain HTTP (an IP or http:// URL): no automatic TLS
PY
}

# Run a command with the caddy unit's Environment= values (RATCHET_SITE_ADDRESS, ...).
with_caddy_env() {
  local -a vars=()
  local line
  if [[ -f "$RATCHET_CADDY_DROPIN" ]]; then
    while IFS= read -r line; do
      [[ "$line" == Environment=* ]] && vars+=("${line#Environment=}")
    done <"$RATCHET_CADDY_DROPIN"
  fi
  env "${vars[@]}" "$@"
}

# /var/log/caddy belongs to the caddy user, and so does every log file in it. The Caddyfile
# writes its access logs there (snippet access_log), and Caddy refuses to start when it cannot
# open one ("setting up custom log ... opening log writer"). Run before validating and again
# before any start or reload.
caddy_log_dir() {
  install -d -m 0755 -o caddy -g caddy /var/log/caddy
  find /var/log/caddy -maxdepth 1 -type f ! -user caddy -exec chown caddy:caddy {} + 2>/dev/null || true
}

# `caddy validate <file>` with the unit's environment, as the caddy user. Validation opens the
# log writers: run as root, it would leave root-owned 0600 log files behind that the service
# (user caddy) then cannot open. Running it as caddy also proves the service can read
# everything the config imports.
caddy_validate() {
  local config="$1"
  caddy_log_dir
  if id -u caddy >/dev/null 2>&1; then
    with_caddy_env runuser -u caddy -- env HOME=/var/lib/caddy \
      caddy validate --config "$config" --adapter caddyfile
  else
    with_caddy_env caddy validate --config "$config" --adapter caddyfile
  fi
}

# Install deploy/vm/Caddyfile if it validates against this host's values, then reload
# Caddy. A Caddyfile that does not validate is left out and the running config is kept.
install_caddyfile() {
  local src="$RATCHET_APP_DIR/deploy/vm/Caddyfile" new=/etc/caddy/Caddyfile.new
  if ! grep -q '^Environment=RATCHET_DEMO_ADDRESS=' "$RATCHET_CADDY_DROPIN" 2>/dev/null; then
    log "caddy: $RATCHET_CADDY_DROPIN predates the apex/demo split; re-run bootstrap.sh with --site-url"
    return 0
  fi
  install -m 0644 "$src" "$new"
  if ! caddy_validate "$new" >/dev/null 2>&1; then
    rm -f "$new"
    log "WARNING: caddy: the new Caddyfile does not validate; keeping the running one"
    return 1
  fi
  if cmp -s "$new" /etc/caddy/Caddyfile; then
    rm -f "$new"
    return 0
  fi
  mv -f "$new" /etc/caddy/Caddyfile
  caddy_reload
}

caddy_reload() {
  if systemctl is-active --quiet caddy; then
    caddy_log_dir
    log "caddy: reload"
    systemctl reload caddy || systemctl restart caddy
  fi
}

# Optional extra domains (RATCHET_ALT_ADDRESSES="ratchetci.com www.ratchetci.com") answer
# with a 301 to the apex. Written as a fragment the Caddyfile imports; removed when unset.
render_caddy_redirects() {
  local apex="$1" alts out="$RATCHET_CADDY_EXTRA_DIR/redirects/alt-domains.caddy" host list=""
  alts="$(env_get RATCHET_ALT_ADDRESSES)"
  install -d -m 0755 "$RATCHET_CADDY_EXTRA_DIR" "$RATCHET_CADDY_EXTRA_DIR/redirects" \
    "$RATCHET_CADDY_EXTRA_DIR/site"
  if [[ -z "${alts// /}" ]]; then
    rm -f "$out"
    return 0
  fi
  for host in ${alts//,/ }; do
    [[ "$host" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "RATCHET_ALT_ADDRESSES: not a host name: $host"
    list="${list:+$list, }$host"
  done
  log "caddy: 301 ${list} -> https://${apex}"
  cat >"$out" <<CADDY
# Generated by deploy/vm/bootstrap.sh from RATCHET_ALT_ADDRESSES; do not edit.
${list} {
	import security_headers
	redir https://${apex}{uri} permanent
}
CADDY
  chmod 0644 "$out"
}

# Build the apex site (deploy/vm/www + the MkDocs site under /docs/ + the benchmark report
# under /bench/) into a new release and switch $RATCHET_SITE_DIR to it atomically.
# Never fatal for the app: a failed docs build keeps the previous /docs/.
render_site() {
  local cache=/var/cache/ratchet releases="${RATCHET_SITE_DIR}.d" release docs="" csp before after
  local build="$cache/docs-site" bench
  bench="$RATCHET_APP_DIR/$(env_get RATCHET_BENCH_REPORT_DIR bench/results/2026-10-16)"
  log "site: mkdocs build (docs dependency group, separate venv)"
  rm -rf "$build"
  if as_ratchet env UV_PROJECT_ENVIRONMENT="$cache/docs-venv" bash -c \
    "cd '$RATCHET_APP_DIR' && uv run --frozen --only-group docs mkdocs build --quiet --site-dir '$build'"; then
    docs="$build"
  elif [[ -d "$RATCHET_SITE_DIR/docs" ]]; then
    log "WARNING: docs build failed; keeping the previous /docs/"
    docs="$(readlink -f "$RATCHET_SITE_DIR")/docs"
  else
    log "WARNING: docs build failed and there is no previous /docs/"
  fi
  release="$releases/$(date -u +%Y%m%dT%H%M%SZ)-$(current_sha | cut -c1-12)"
  install -d -m 0755 "$releases"
  rm -rf "$release"
  csp="$RATCHET_CADDY_EXTRA_DIR/site/docs-csp.caddy"
  install -d -m 0755 "$RATCHET_CADDY_EXTRA_DIR" "$RATCHET_CADDY_EXTRA_DIR/site" \
    "$RATCHET_CADDY_EXTRA_DIR/redirects"
  before="$(sha256sum "$csp" 2>/dev/null || true)"
  # Explicit checks: callers may run this under `||`, where `set -e` does not apply.
  python3 -I "$RATCHET_APP_DIR/deploy/vm/render_site.py" \
    --app-dir "$RATCHET_APP_DIR" --out "$release" ${docs:+--docs "$docs"} --bench "$bench" \
    --repo-url "$(env_get RATCHET_REPO_URL https://github.com/lloydjf/ratchet)" \
    --ref "$(current_sha)" --csp-out "$csp" || {
    rm -rf "$release"
    log "ERROR: render_site.py failed; the previous site stays live"
    return 1
  }
  chown -R root:root "$release" && chmod -R u=rwX,go=rX "$release" \
    && ln -sfn "$release" "${RATCHET_SITE_DIR}.tmp" \
    && mv -Tf "${RATCHET_SITE_DIR}.tmp" "$RATCHET_SITE_DIR" || return 1
  # Keep the two newest releases (the live one and the one before it).
  find "$releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r | tail -n +3 \
    | while read -r old; do rm -rf "${releases:?}/${old:?}"; done
  after="$(sha256sum "$csp" 2>/dev/null || true)"
  log "site: live at $RATCHET_SITE_DIR -> ${release##*/}"
  [[ "$before" == "$after" ]] || caddy_reload
}

# --- edge mode (RATCHET_EDGE) --------------------------------------------------------------------
#   caddy   Caddy is public on 80/443 with ACME for the apex, www and demo; the apex site is
#           rendered on this VM (render_site). DNS: A records to the VM, DNS only.
#   tunnel  Cloudflare is public: the apex is Cloudflare static assets (deploy/site/), the demo
#           reaches this VM through ratchet-tunnel.service; Caddy serves the demo host as plain
#           HTTP on 127.0.0.1:80 only. No inbound port but SSH.

edge_mode() {
  local mode
  mode="$(env_get RATCHET_EDGE caddy)"
  case "$mode" in
    caddy | tunnel) printf '%s' "$mode" ;;
    *) die "RATCHET_EDGE must be caddy or tunnel, not: $mode" ;;
  esac
}

# Write the caddy unit's drop-in for the current edge mode, render what that mode serves,
# install and validate the Caddyfile, restart Caddy, and start (tunnel) or stop (caddy) the
# tunnel connector. Used by bootstrap.sh and by deploy.sh --edge. Fatal on an invalid config.
configure_edge() {
  local edge demo apex upstream
  edge="$(edge_mode)"
  demo="$(site_address "$(env_get RATCHET_PUBLIC_URL)")"
  apex="$(site_address "$(env_get RATCHET_SITE_URL)")"
  upstream="$(sed -n 's/^Environment=RATCHET_UPSTREAM_ADDR=//p' "$RATCHET_CADDY_DROPIN" 2>/dev/null || true)"
  if [[ "$edge" == "tunnel" ]]; then
    [[ "$demo" != :* ]] || die "RATCHET_EDGE=tunnel needs RATCHET_PUBLIC_URL=https://<demo host> (Cloudflare holds the certificate)"
    demo="${demo%%:*}"
    log "caddy (edge=tunnel): http://$demo on 127.0.0.1:80 and [::1]:80 only -> ${upstream:-127.0.0.1:8080}; the apex, www, /docs/ and /bench/ are on Cloudflare"
  else
    [[ "$demo" == :* ]] && log "WARNING: RATCHET_PUBLIC_URL is not https://<host>; serving the demo as plain HTTP on $demo"
    [[ "$apex" == :* ]] && die "RATCHET_SITE_URL must be https://<domain> (www.<domain> redirects to it)"
    log "caddy (edge=caddy): $apex (static site), www.$apex (301), $demo -> ${upstream:-127.0.0.1:8080}"
  fi
  install -m 0644 "$RATCHET_APP_DIR/deploy/vm/Caddyfile" /etc/caddy/Caddyfile
  install -d -m 0755 "$(dirname "$RATCHET_CADDY_DROPIN")"
  cat >"$RATCHET_CADDY_DROPIN" <<EOF
[Service]
Environment=RATCHET_EDGE=${edge}
Environment=RATCHET_SITE_ADDRESS=${apex}
Environment=RATCHET_DEMO_ADDRESS=${demo}
Environment=RATCHET_UPSTREAM_ADDR=${upstream:-127.0.0.1:8080}
EOF
  render_fallback
  if [[ "$edge" == "caddy" ]]; then
    render_caddy_redirects "$apex"
    render_site || log "WARNING: site build failed; the previous site (if any) stays live"
  fi
  systemctl daemon-reload
  caddy_validate /etc/caddy/Caddyfile >/dev/null \
    || die "caddy: /etc/caddy/Caddyfile does not validate for RATCHET_EDGE=$edge"
  caddy_log_dir
  systemctl enable --quiet caddy
  systemctl restart caddy || die "caddy did not start: journalctl -u caddy -n 50 --no-pager"
  if [[ "$edge" == "tunnel" ]]; then
    "$RATCHET_APP_DIR/deploy/vm/edge-tunnel.sh" install \
      || log "WARNING: tunnel connector not running yet: write the token (sudo deploy/vm/edge-tunnel.sh set-token < token.txt), then sudo deploy/vm/edge-tunnel.sh install"
  elif systemctl is-enabled --quiet "$RATCHET_TUNNEL_UNIT" 2>/dev/null; then
    log "edge=caddy: stopping and disabling $RATCHET_TUNNEL_UNIT"
    systemctl disable --now --quiet "$RATCHET_TUNNEL_UNIT"
  fi
}

# ufw for the edge mode. RATCHET_ENABLE_UFW (process environment, else /etc/ratchet/env):
#   1 = on, 0 = off, empty = the mode's default: on in tunnel mode (cloudflared only dials out,
#   so nothing but SSH needs to be reachable), off in caddy mode (the security group decides).
# On: default deny incoming; caddy allows 22, 80, 443; tunnel allows 22/tcp only and removes
# 80/443. SSH on another port: allow it yourself first (ufw allow <port>/tcp).
ufw_wanted() {
  local value
  value="$(env_get RATCHET_ENABLE_UFW)"
  case "$value" in
    1 | 0) [[ "$value" == "1" ]] ;;
    "") [[ "$(edge_mode)" == "tunnel" ]] ;;
    *) die "RATCHET_ENABLE_UFW must be 1, 0 or empty, not: $value" ;;
  esac
}

edge_firewall() {
  local edge
  edge="$(edge_mode)"
  if ! ufw_wanted; then
    log "ufw: not managed (RATCHET_ENABLE_UFW=$(env_get RATCHET_ENABLE_UFW '<empty>'), edge=$edge)"
    return 0
  fi
  command -v ufw >/dev/null || apt-get install -y -q ufw
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow 22/tcp >/dev/null
  if [[ "$edge" == "tunnel" ]]; then
    log "ufw (edge=tunnel): allow 22/tcp only; deny the rest inbound"
    ufw delete allow 80/tcp >/dev/null 2>&1 || true
    ufw delete allow 443/tcp >/dev/null 2>&1 || true
  else
    log "ufw (edge=caddy): allow 22/tcp, 80/tcp, 443/tcp; deny the rest inbound"
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
  fi
  # Replay sandboxes live on the internal Docker network and reach the worker's proxy and
  # mock-tool sidecars on its gateway; ufw's default deny would drop that traffic and every
  # replay would time out. Allow only sandbox subnet -> gateway on that bridge.
  local subnet gateway bridge
  subnet="${RATCHET_SANDBOX_SUBNET:-172.30.0.0/24}"
  gateway="${RATCHET_SANDBOX_GATEWAY:-172.30.0.1}"
  bridge="$(docker network inspect "${RATCHET_SANDBOX_NETWORK:-ratchet-sandbox}" \
    --format '{{index .Options "com.docker.network.bridge.name"}}' 2>/dev/null || true)"
  if [[ -z "$bridge" ]]; then
    bridge="$(docker network inspect "${RATCHET_SANDBOX_NETWORK:-ratchet-sandbox}" \
      --format '{{.Id}}' 2>/dev/null | cut -c1-12 || true)"
    [[ -n "$bridge" ]] && bridge="br-$bridge"
  fi
  if [[ -n "$bridge" ]]; then
    ufw allow in on "$bridge" from "$subnet" to "$gateway" comment "ratchet sandbox sidecars" >/dev/null
    log "ufw: sandbox sidecars allowed on $bridge ($subnet -> $gateway)"
  else
    log "ufw: sandbox network not found yet; re-run bootstrap after the sandbox network exists"
  fi
  ufw --force enable >/dev/null
  log "ufw: $(ufw status | head -n 1)"
}
