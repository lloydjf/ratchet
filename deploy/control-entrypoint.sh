#!/usr/bin/env bash
# Entrypoint of the ratchet-control image.
#   serve (default)   ratchet db migrate -> ratchet seed-demo (idempotent) -> exec ratchet serve
#   <ratchet cmd>     exec ratchet <cmd> ...      e.g. `docker run ratchet-control doctor`
#   bash|sh|python    exec as-is (debugging)
# RATCHET_SEED_ON_START=false skips seed-demo (e.g. live upstream before keys are set).
# The image is the demo stack, so Simulate is on unless the environment says otherwise
# (the Settings default is off).
set -euo pipefail

export RATCHET_SIMULATE_ENABLED="${RATCHET_SIMULATE_ENABLED:-true}"

log() { printf '%s ratchet-entrypoint: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

skills="${RATCHET_SKILLS_REPO:-/srv/ratchet/skills}"
mkdir -p "${RATCHET_DATA_DIR:-/srv/ratchet/data}" "${skills%/*}" "${TMPDIR:-/tmp}"

[[ $# -eq 0 ]] && set -- serve
cmd="$1"
case "$cmd" in
  serve)
    log "ratchet db migrate"
    ratchet db migrate
    if [[ "${RATCHET_SEED_ON_START:-true}" == "true" ]]; then
      log "ratchet seed-demo (idempotent; upstream=${RATCHET_UPSTREAM:-sim})"
      ratchet seed-demo
    fi
    log "exec ratchet $*"
    exec ratchet "$@"
    ;;
  bash | sh | python | python3)
    exec "$@"
    ;;
  *)
    exec ratchet "$@"
    ;;
esac
