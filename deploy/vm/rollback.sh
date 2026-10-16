#!/usr/bin/env bash
# Roll back to the previous good deploy.
#
#   sudo /opt/ratchet/deploy/vm/rollback.sh            # previous PASS in the deploy ledger
#   sudo /opt/ratchet/deploy/vm/rollback.sh <sha>      # a specific commit
#   sudo /opt/ratchet/deploy/vm/rollback.sh --with-restore <YYYY-MM-DD> [<sha>]
#
# Checks out the target, rebuilds the dashboard, restarts and runs the same smoke as a deploy
# (deploy.sh --kind rollback, which skips the CI gate). Migrations are forward-only: if the
# older code cannot run on the newer schema, use --with-restore to also restore the nightly
# backup of that date (restore.sh) before the smoke.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"
reexec_from_copy "${BASH_SOURCE[0]}" "$@"

TARGET=""
RESTORE_DATE=""
while (($#)); do
  case "$1" in
    --with-restore) RESTORE_DATE="${2:?--with-restore needs YYYY-MM-DD}"; shift 2 ;;
    -h | --help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) TARGET="$1"; shift ;;
  esac
done

require_root
take_lock

# Newest ledger row that is good (PASS for deploy/rollback, ok for bootstrap) and not current.
previous_good() {
  local current="$1"
  [[ -f "$RATCHET_LEDGER" ]] || return 1
  tac "$RATCHET_LEDGER" | awk -F'\t' -v cur="$current" \
    '($5 == "PASS" || $5 == "ok") && $3 != cur && $3 != "unknown" { print $3; exit }'
}

main() {
  local current
  current="$(current_sha)"
  if [[ -z "$TARGET" ]]; then
    TARGET="$(previous_good "$current" || true)"
    [[ -n "$TARGET" ]] || die "no previous good deploy in $RATCHET_LEDGER; pass a sha"
  fi
  log "rolling back from ${current:0:12} to ${TARGET:0:12}"
  if [[ -n "$RESTORE_DATE" ]]; then
    "$SCRIPT_DIR/restore.sh" "$RESTORE_DATE" --no-start
  fi
  "$SCRIPT_DIR/deploy.sh" "$TARGET" --kind rollback
}

main
