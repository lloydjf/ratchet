#!/usr/bin/env bash
# Bootstrap the Ratchet host on a fresh Nebius CPU VM (Ubuntu 24.04): the demo app on
# demo.<domain> and, in caddy mode, the static product page + docs + benchmark on the apex.
# Idempotent: safe to re-run after a failure or to converge an existing host. Never
# overwrites a value already in /etc/ratchet/env (except the URLs and the edge, when passed).
#
#   git clone https://github.com/<org>/ratchet /tmp/ratchet
#   sudo /tmp/ratchet/deploy/vm/bootstrap.sh --public-url https://demo.ratchetci.dev \
#        --site-url https://ratchetci.dev [--edge caddy|tunnel] [--ufw on|off] \
#        [--repo https://github.com/<org>/ratchet] [--ref main]
#
# --edge (RATCHET_EDGE, kept in /etc/ratchet/env; default caddy):
#   caddy   Caddy is public on 80/443 (ACME) for the apex, www and demo; the apex site is built
#           here. Point DNS (A records, DNS only) at this VM *before* the first run if you want
#           TLS on the first try (deploy/vm/dns.md; Caddy keeps retrying on its own otherwise).
#   tunnel  Cloudflare is public: the apex is Cloudflare static assets (.github/workflows/
#           site.yml), demo.<apex> comes in through a Cloudflare Tunnel (edge-tunnel.sh) to
#           Caddy on 127.0.0.1:80. Write the tunnel token first (sudo deploy/vm/edge-tunnel.sh
#           set-token < token.txt) or later, then `sudo deploy/vm/edge-tunnel.sh install`.
#
# Then: put keys in /etc/ratchet/env (sudoedit), `sudo systemctl restart ratchet-serve`.
# Serverless Jobs: copy the service-account credentials to /etc/ratchet/nebius-sa.json
# (root 0600) first, or later and then `sudo systemctl start ratchet-nebius-token`.
#
# Installs: git, curl, python3 (for ops helpers), uv 0.8.18 (pinned sha256) + Python 3.11
# (uv-managed), Node 24 (NodeSource apt repo), Docker CE (docker.com apt repo), Caddy
# (Caddy's apt repo); the Nebius CLI (pinned version, official script) when the host uses
# Serverless Jobs; cloudflared (Cloudflare's apt repo, key fingerprint pinned) in tunnel mode.
# No unpinned `curl | sh`.
# --ufw on|off sets RATCHET_ENABLE_UFW in /etc/ratchet/env. Empty (the default): on in tunnel
# mode (22/tcp only), off in caddy mode; on in caddy mode allows 22, 80 and 443. An env var
# works too, but only inside sudo (`sudo RATCHET_ENABLE_UFW=1 bootstrap.sh`): sudo drops
# variables set before it. Other knobs: RATCHET_SKIP_SEED=1, RATCHET_SKIP_WORKER_IMAGE=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

UV_VERSION="0.8.18"
declare -A UV_SHA256=(
  [x86_64]="59ad1a1809fa47019b86cf339fff161cb7b00ad3d8d42354eea57d0d91aeb44c"
  [aarch64]="164363f88618f4e8e175faf9fcf5172321c221fce93d64cec8e9ab3339511dad"
)
NODE_MAJOR="24"

REPO_URL=""
REF="main"
PUBLIC_URL=""
SITE_URL=""
EDGE=""
UFW=""

usage() {
  sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while (($#)); do
  case "$1" in
    --repo) REPO_URL="${2:?--repo needs a URL}"; shift 2 ;;
    --ref) REF="${2:?--ref needs a branch, tag or sha}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:?--public-url needs https://demo.<domain>}"; shift 2 ;;
    --site-url) SITE_URL="${2:?--site-url needs https://<domain>}"; shift 2 ;;
    --edge) EDGE="${2:?--edge needs caddy or tunnel}"; shift 2 ;;
    --ufw) UFW="${2:?--ufw needs on or off}"; shift 2 ;;
    -h | --help) usage 0 ;;
    *) log "unknown argument: $1"; usage 2 ;;
  esac
done

[[ -z "$EDGE" || "$EDGE" == "caddy" || "$EDGE" == "tunnel" ]] || die "--edge must be caddy or tunnel"
[[ -z "$UFW" || "$UFW" == "on" || "$UFW" == "off" ]] || die "--ufw must be on or off"
require_root
take_lock 1800
# shellcheck source=/dev/null
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || log "WARNING: tested on Ubuntu 24.04, this is ${PRETTY_NAME:-unknown}"

# --- 1. packages ----------------------------------------------------------------------------
apt_repos() {
  local arch codename
  arch="$(dpkg --print-architecture)"
  codename="${VERSION_CODENAME:-noble}"
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  fi
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    >/etc/apt/sources.list.d/docker.list
  if [[ ! -s /etc/apt/keyrings/nodesource.gpg ]]; then
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  fi
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
    >/etc/apt/sources.list.d/nodesource.list
  if [[ ! -s /usr/share/keyrings/caddy-stable-archive-keyring.gpg ]]; then
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
      | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  fi
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    -o /etc/apt/sources.list.d/caddy-stable.list
}

install_packages() {
  log "installing base packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends ca-certificates curl gnupg git python3 tar gzip \
    debian-keyring debian-archive-keyring apt-transport-https util-linux
  apt_repos
  apt-get update -q
  apt-get install -y -q --no-install-recommends nodejs caddy \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin
  systemctl enable --now docker
}

install_uv() {
  local arch tarball tmp
  if command -v uv >/dev/null && [[ "$(uv --version | awk '{print $2}')" == "$UV_VERSION" ]]; then
    log "uv $UV_VERSION already installed"
    return
  fi
  arch="$(uname -m)"
  [[ -n "${UV_SHA256[$arch]:-}" ]] || die "no pinned uv checksum for $arch"
  tarball="uv-${arch}-unknown-linux-gnu.tar.gz"
  tmp="$(mktemp -d)"
  log "installing uv $UV_VERSION ($arch, sha256 pinned)"
  curl -fsSL -o "$tmp/$tarball" \
    "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${tarball}"
  echo "${UV_SHA256[$arch]}  $tmp/$tarball" | sha256sum -c - >/dev/null
  tar -xzf "$tmp/$tarball" -C "$tmp"
  install -m 0755 "$tmp/uv-${arch}-unknown-linux-gnu/uv" "$tmp/uv-${arch}-unknown-linux-gnu/uvx" \
    /usr/local/bin/
  rm -rf "$tmp"
}

# --- 2. user and directories -------------------------------------------------------------------
setup_user_and_dirs() {
  if ! id -u "$RATCHET_USER" >/dev/null 2>&1; then
    log "creating system user $RATCHET_USER"
    useradd --system --home-dir "$RATCHET_SRV_DIR" --no-create-home \
      --shell /usr/sbin/nologin "$RATCHET_USER"
  fi
  install -d -m 0755 -o "$RATCHET_USER" -g "$RATCHET_USER" "$RATCHET_APP_DIR"
  install -d -m 0750 -o "$RATCHET_USER" -g "$RATCHET_USER" "$RATCHET_SRV_DIR" \
    "$RATCHET_SRV_DIR/data" "$RATCHET_SRV_DIR/tmp"
  install -d -m 0755 -o "$RATCHET_USER" -g "$RATCHET_USER" /opt/uv "$UV_PYTHON_INSTALL_DIR" \
    /var/cache/ratchet "$UV_CACHE_DIR" "$RATCHET_NPM_CACHE"
  install -d -m 0700 -o root -g root /etc/ratchet "$RATCHET_BACKUP_DIR"
  install -d -m 0755 -o root -g root "$RATCHET_OPS_DIR" "$RATCHET_FALLBACK_DIR"
}

# --- 3. code ------------------------------------------------------------------------------------
checkout_code() {
  if [[ ! -d "$RATCHET_APP_DIR/.git" ]]; then
    if [[ -z "$REPO_URL" ]]; then
      REPO_URL="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
    fi
    [[ -n "$REPO_URL" ]] || die "--repo is required for the first run"
    log "cloning $REPO_URL into $RATCHET_APP_DIR"
    as_ratchet git clone --quiet "$REPO_URL" "$RATCHET_APP_DIR"
  fi
  log "checking out $REF"
  deploys_md_reset
  app_git fetch --quiet --tags origin
  if app_git rev-parse --verify --quiet "origin/$REF" >/dev/null; then
    app_git checkout --quiet --detach "origin/$REF"
  else
    app_git checkout --quiet --detach "$REF"
  fi
  deploys_md_sync
}

# --- 4. /etc/ratchet/env --------------------------------------------------------------------------
setup_env_file() {
  local template="$RATCHET_APP_DIR/deploy/vm/env.template" key line
  if [[ ! -f "$RATCHET_ENV_FILE" ]]; then
    log "creating $RATCHET_ENV_FILE from env.template (root:root 0600)"
    install -m 0600 -o root -g root "$template" "$RATCHET_ENV_FILE"
  fi
  chmod 0600 "$RATCHET_ENV_FILE"
  chown root:root "$RATCHET_ENV_FILE"
  # Append keys added to the template since the last run; never touch existing values.
  while IFS= read -r line; do
    [[ "$line" =~ ^([A-Z0-9_]+)= ]] || continue
    key="${BASH_REMATCH[1]}"
    grep -q "^${key}=" "$RATCHET_ENV_FILE" || printf '%s\n' "$line" >>"$RATCHET_ENV_FILE"
  done <"$template"
  if [[ -n "$PUBLIC_URL" ]]; then
    env_set RATCHET_PUBLIC_URL "$PUBLIC_URL"
  fi
  if [[ -n "$SITE_URL" ]]; then
    env_set RATCHET_SITE_URL "$SITE_URL"
  fi
  if [[ -n "$EDGE" ]]; then
    env_set RATCHET_EDGE "$EDGE"
  fi
  if [[ -n "$UFW" ]]; then
    env_set RATCHET_ENABLE_UFW "$([[ "$UFW" == "on" ]] && echo 1 || echo 0)"
  fi
  edge_mode >/dev/null
  if [[ -z "$(env_get RATCHET_ACTION_TOKEN_SECRET)" ]]; then
    log "generating RATCHET_ACTION_TOKEN_SECRET"
    env_set RATCHET_ACTION_TOKEN_SECRET "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  fi
  [[ -n "$(env_get RATCHET_PUBLIC_URL)" ]] || die "set --public-url (or RATCHET_PUBLIC_URL in $RATCHET_ENV_FILE)"
  # caddy mode serves the apex here; in tunnel mode it is only used for checks and messages.
  if [[ "$(edge_mode)" == "caddy" ]]; then
    [[ "$(env_get RATCHET_SITE_URL)" == https://* ]] \
      || die "set --site-url https://<domain> (or RATCHET_SITE_URL in $RATCHET_ENV_FILE)"
  fi
}

# --- 5. build ------------------------------------------------------------------------------------
build_app() {
  log "uv sync --frozen --no-dev (Python 3.11, uv-managed)"
  as_ratchet bash -c "cd '$RATCHET_APP_DIR' && uv python install 3.11 && uv sync --frozen --no-dev"
  log "dashboard: npm ci && npm run build"
  as_ratchet bash -c "cd '$RATCHET_APP_DIR/dashboard' && npm ci --no-audit --no-fund && npm run build"
}

setup_docker_sandbox() {
  local sandbox runner image
  sandbox="$(env_get RATCHET_SANDBOX process)"
  runner="$(env_get RATCHET_LOCAL_RUNNER_MODE process)"
  if [[ "$sandbox" != "docker" && "$runner" != "docker" ]]; then
    return
  fi
  # Docker group membership is root-equivalent; it is what the Docker sandbox needs (ADR 0010).
  usermod -aG docker "$RATCHET_USER"
  "$RATCHET_APP_DIR/deploy/sandbox-network.sh"
  [[ "${RATCHET_SKIP_WORKER_IMAGE:-0}" == "1" ]] && return
  image="$(env_get RATCHET_WORKER_IMAGE ratchet-worker:dev)"
  if [[ "$image" == */* ]]; then
    log "pulling worker image $image"
    docker pull --quiet "$image"
  else
    log "building worker image $image locally"
    docker build --quiet -f "$RATCHET_APP_DIR/worker/Dockerfile" -t "$image" "$RATCHET_APP_DIR"
  fi
}

# The Nebius CLI mints the IAM token for Serverless Jobs (nebius-token.sh). Installed when
# the host is set up for Jobs; otherwise nebius-token.sh installs it once credentials appear.
install_nebius_cli() {
  if [[ -e /etc/ratchet/nebius-sa.json || "$(env_get RATCHET_JOB_RUNNER local)" == "nebius" ]]; then
    "$RATCHET_APP_DIR/deploy/vm/nebius-token.sh" install
  fi
}

install_backup_tools() {
  local bucket
  bucket="$(env_get RATCHET_BACKUP_S3_BUCKET "$(env_get NEBIUS_S3_BUCKET)")"
  if [[ -n "$bucket" ]] && ! command -v aws >/dev/null; then
    log "installing awscli (uv tool) for backup upload to Nebius Object Storage"
    UV_TOOL_DIR=/opt/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin uv tool install --quiet awscli
  fi
}

# --- 6. systemd, journald, caddy ------------------------------------------------------------------
install_units() {
  local unit
  log "installing systemd units"
  for unit in "$RATCHET_APP_DIR"/deploy/vm/systemd/*.{service,timer}; do
    install -m 0644 "$unit" "/etc/systemd/system/${unit##*/}"
  done
  install -d -m 0755 /etc/systemd/journald.conf.d
  install -m 0644 "$RATCHET_APP_DIR/deploy/vm/journald/ratchet.conf" \
    /etc/systemd/journald.conf.d/ratchet.conf
  systemctl restart systemd-journald
  systemctl daemon-reload
  # Re-enable so Alias= symlinks (ratchet-proxy, ratchet-orchestrator, ...) are refreshed.
  systemctl reenable ratchet-serve.service >/dev/null 2>&1 || systemctl enable ratchet-serve.service
  systemctl enable ratchet-backup.timer ratchet-token-rotate.timer ratchet-alerts.timer \
    ratchet-nebius-token.timer
}

# The edge mode's Caddy config, fallback page, apex site (caddy mode) and tunnel connector
# (tunnel mode): configure_edge in lib.sh, shared with deploy.sh --edge.
setup_caddy() {
  configure_edge
}

setup_firewall() {
  edge_firewall
}

# --- 7. data + start -------------------------------------------------------------------------------
init_data_and_start() {
  log "ratchet db migrate"
  ratchet_cli db migrate
  if [[ "${RATCHET_SKIP_SEED:-0}" != "1" ]]; then
    log "ratchet seed-demo (idempotent; upstream=$(env_get RATCHET_UPSTREAM sim))"
    ratchet_cli seed-demo
  fi
  # A fresh IAM token before the first start (no-op without /etc/ratchet/nebius-sa.json).
  "$RATCHET_APP_DIR/deploy/vm/nebius-token.sh" refresh \
    || log "WARNING: Nebius token refresh failed; see deploy/vm/nebius-token.sh status"
  systemctl daemon-reload
  systemctl enable ratchet-serve.service
  systemctl restart ratchet-serve.service
  systemctl start ratchet-backup.timer ratchet-token-rotate.timer ratchet-alerts.timer \
    ratchet-nebius-token.timer
  wait_healthy "$RATCHET_LOCAL_URL" 120 || die "ratchet-serve is not healthy; journalctl -u ratchet-serve -n 200"
}

main() {
  install_packages
  install_uv
  setup_user_and_dirs
  checkout_code
  setup_env_file
  # Before anything that can fail late (builds, Caddy): a broken step must not leave the
  # firewall unconfigured.
  setup_firewall
  build_app
  setup_docker_sandbox
  install_backup_tools
  install_nebius_cli
  install_units
  init_data_and_start
  setup_caddy
  ledger_append bootstrap "$(current_sha)" ok "ref=$REF"
  deploys_md_sync
  local public site edge pending
  public="$(env_get RATCHET_PUBLIC_URL)"
  site="$(env_get RATCHET_SITE_URL)"
  edge="$(edge_mode)"
  if [[ "$edge" == "tunnel" ]]; then
    pending="tunnel token, connector or its public hostname pending? deploy/vm/edge-tunnel.sh status"
  else
    pending="DNS or certificate pending? Caddy keeps retrying"
  fi
  if wait_healthy "$public" 30; then
    log "demo URL is live: $public"
  else
    log "demo URL not reachable yet ($pending): $public"
  fi
  if [[ -n "$site" ]]; then
    if curl -fsS -o /dev/null --max-time 5 "$site/"; then
      log "site URL is live: $site"
    elif [[ "$edge" == "tunnel" ]]; then
      log "site URL not reachable yet: $site is Cloudflare static assets (.github/workflows/site.yml), not this VM"
    else
      log "site URL not reachable yet (DNS or certificate pending?): $site"
    fi
  fi
  log "done (edge=$edge). Next: keys in $RATCHET_ENV_FILE, then deploy/vm/deploy.sh <sha>; DNS: deploy/vm/dns.md; uptime monitor: deploy/vm/uptime.md"
}

main
