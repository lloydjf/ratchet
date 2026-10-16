#!/usr/bin/env bash
# Deploy a commit to the demo host (the deploy procedure in docs/RUNBOOK.md, steps 1-6).
#
#   sudo /opt/ratchet/deploy/vm/deploy.sh <git-sha|tag|branch> [--skip-ci-check] [--edge caddy|tunnel]
#
#   1. CI must be green for the commit (GitHub check runs; --skip-ci-check to bypass, and
#      rollbacks skip it). If RATCHET_WORKER_IMAGE_REPO is set in /etc/ratchet/env (e.g.
#      ghcr.io/<org>/ratchet-worker), the CI-built <repo>:<sha> is pulled and pinned;
#      otherwise a local worker tag is rebuilt from the checkout when the sandbox is docker.
#   2. git fetch + checkout --detach <sha>; uv sync --frozen --no-dev; npm ci && npm run build.
#      RATCHET_EDGE=caddy: rebuild the apex site, MkDocs (/docs/), the benchmark report
#      (/bench/) and deploy/vm/www (render_site in lib.sh). tunnel: the apex is Cloudflare
#      static assets (.github/workflows/site.yml), nothing to build here. Both: install the
#      Caddyfile if it validates; reload. --edge switches the mode first (the env file, the
#      Caddy drop-in, the tunnel connector: configure_edge in lib.sh), as bootstrap.sh --edge.
#   3. ratchet db migrate (forward-only, idempotent).
#   4. restart ratchet-serve (one process: proxy -> mock tools -> orchestrator -> dashboard
#      start in that order inside `ratchet serve`), wait for /healthz.
#   5. smoke: `ratchet doctor`, then `ratchet simulate 1 --headless` must return the
#      scenario's expected BLOCK within 180 s (idle runs take 41–72 s; the budget is a health check, not a latency claim).
#   6. record sha, time, who, smoke result in the ledger and docs/DEPLOYS.md; notify.
#
# Exit 0 on PASS. On a failed smoke: exit 1 and print the rollback command, or roll back
# automatically when RATCHET_AUTO_ROLLBACK=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"
reexec_from_copy "${BASH_SOURCE[0]}" "$@"

REF=""
KIND="deploy"
SKIP_CI=0
EDGE=""
while (($#)); do
  case "$1" in
    --skip-ci-check) SKIP_CI=1; shift ;;
    --edge) EDGE="${2:?--edge needs caddy or tunnel}"; shift 2 ;;
    --kind) KIND="${2:?--kind needs deploy|rollback}"; shift 2 ;;
    -h | --help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) REF="$1"; shift ;;
  esac
done
[[ -n "$REF" ]] || die "usage: deploy.sh <git-sha> [--skip-ci-check] [--edge caddy|tunnel]"
[[ -z "$EDGE" || "$EDGE" == "caddy" || "$EDGE" == "tunnel" ]] || die "--edge must be caddy or tunnel"

require_root
take_lock

# GitHub check runs for <sha>: all completed and success/skipped/neutral. Non-GitHub origins
# or an unreachable API warn and continue unless RATCHET_REQUIRE_CI=1.
check_ci() {
  local sha="$1" origin
  origin="$(app_git remote get-url origin 2>/dev/null || true)"
  python3 - "$origin" "$sha" "${GITHUB_TOKEN:-}" <<'PY'
import json, re, sys, urllib.request
origin, sha, token = sys.argv[1:4]
m = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", origin)
if not m:
    print(f"origin {origin!r} is not on GitHub; CI status not checked", file=sys.stderr)
    sys.exit(2)
url = f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/commits/{sha}/check-runs?per_page=100"
req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
if token:
    req.add_header("Authorization", f"Bearer {token}")
try:
    runs = json.load(urllib.request.urlopen(req, timeout=15))["check_runs"]
except Exception as exc:  # network, rate limit, private repo
    print(f"could not read check runs: {exc}", file=sys.stderr)
    sys.exit(2)
bad = [r["name"] for r in runs if r["status"] != "completed"
       or r["conclusion"] not in ("success", "skipped", "neutral")]
if not runs or bad:
    print(f"CI not green for {sha[:12]}: {bad or 'no check runs'}", file=sys.stderr)
    sys.exit(1)
print(f"CI green for {sha[:12]} ({len(runs)} check runs)", file=sys.stderr)
PY
}

prepare_worker_image() {
  local sha="$1" sandbox runner repo image
  sandbox="$(env_get RATCHET_SANDBOX process)"
  runner="$(env_get RATCHET_LOCAL_RUNNER_MODE process)"
  repo="$(env_get RATCHET_WORKER_IMAGE_REPO)"
  if [[ -n "$repo" ]]; then
    image="${repo}:${sha}"
    log "pulling CI-built worker image $image"
    docker pull --quiet "$image" >/dev/null
    env_set RATCHET_WORKER_IMAGE "$image"
  elif [[ "$sandbox" == "docker" || "$runner" == "docker" ]]; then
    image="$(env_get RATCHET_WORKER_IMAGE ratchet-worker:dev)"
    if [[ "$image" != */* ]]; then
      log "rebuilding local worker image $image from $sha"
      docker build --quiet -f "$RATCHET_APP_DIR/worker/Dockerfile" -t "$image" "$RATCHET_APP_DIR" >/dev/null
    fi
  fi
}

smoke() {
  local started
  log "smoke: ratchet doctor"
  RATCHET_CLI_TIMEOUT=60 ratchet_cli doctor || return 1
  log "smoke: ratchet simulate 1 --headless (must BLOCK within 180 s)"
  started=$SECONDS
  RATCHET_CLI_TIMEOUT=180 ratchet_cli simulate 1 --headless || return 1
  SMOKE_SECS=$((SECONDS - started))
}

main() {
  local sha prev result note
  log "fetching origin"
  app_git fetch --quiet --tags origin
  sha="$(app_git rev-parse --verify --quiet "${REF}^{commit}" \
    || app_git rev-parse --verify --quiet "origin/${REF}^{commit}")" \
    || die "unknown commit: $REF"
  prev="$(current_sha)"
  log "$KIND $sha (current $prev)"

  if [[ "$KIND" == "deploy" && "$SKIP_CI" == "0" ]]; then
    local ci=0
    check_ci "$sha" || ci=$?
    if ((ci == 1)) || { ((ci == 2)) && [[ "${RATCHET_REQUIRE_CI:-0}" == "1" ]]; }; then
      die "refusing to deploy $sha: CI is not green (use --skip-ci-check to override)"
    fi
  fi

  deploys_md_reset
  app_git checkout --quiet --detach "$sha"
  log "uv sync --frozen --no-dev"
  as_ratchet bash -c "cd '$RATCHET_APP_DIR' && uv sync --frozen --no-dev"
  log "dashboard: npm ci && npm run build"
  as_ratchet bash -c "cd '$RATCHET_APP_DIR/dashboard' && npm ci --no-audit --no-fund && npm run build"
  prepare_worker_image "$sha"

  log "ratchet db migrate"
  ratchet_cli db migrate

  log "restarting $RATCHET_UNIT"
  install -m 0644 "$RATCHET_APP_DIR"/deploy/vm/systemd/*.{service,timer} /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --quiet --now ratchet-nebius-token.timer
  systemctl restart "$RATCHET_UNIT"
  if [[ -n "$EDGE" ]]; then
    log "switching the edge to $EDGE"
    env_set RATCHET_EDGE "$EDGE"
    configure_edge
  else
    render_fallback
    # The apex is static: a failure here is logged and never fails the app deploy.
    if [[ "$(edge_mode)" == "caddy" ]]; then
      render_site || log "WARNING: site build failed; the previous site stays live"
    fi
    install_caddyfile || true
  fi

  SMOKE_SECS=0
  if wait_healthy "$RATCHET_LOCAL_URL" 90 && smoke; then
    result="PASS"
  else
    result="FAIL"
  fi
  note="prev=${prev:0:12} smoke=${SMOKE_SECS}s edge=$(edge_mode)"
  ledger_append "$KIND" "$sha" "$result" "$note"
  deploys_md_sync
  notify "Ratchet $KIND ${sha:0:12}: $result ($note) on $(hostname)"
  log "$KIND ${sha:0:12}: $result ($note)"

  if [[ "$result" == "FAIL" ]]; then
    if [[ "$KIND" == "deploy" && "${RATCHET_AUTO_ROLLBACK:-0}" == "1" ]]; then
      log "RATCHET_AUTO_ROLLBACK=1: rolling back"
      "$SCRIPT_DIR/rollback.sh"
    fi
    log "smoke failed. Logs: journalctl -u $RATCHET_UNIT -n 200. Roll back: sudo $RATCHET_APP_DIR/deploy/vm/rollback.sh"
    exit 1
  fi
}

main
