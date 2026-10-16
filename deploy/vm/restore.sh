#!/usr/bin/env bash
# Restore /srv/ratchet/{data,skills} from a nightly backup.
#
#   sudo /opt/ratchet/deploy/vm/restore.sh <YYYY-MM-DD> [--no-start]
#
# Looks for /var/backups/ratchet/ratchet-<date>.tar.gz, else downloads it from object
# storage; verifies the sha256; stops ratchet-serve; moves the current data/ and skills/ to
# /srv/ratchet/pre-restore-<utc-ts>/ (nothing is deleted); unpacks; puts the SQLite
# snapshots back in place; starts the service and waits for /healthz (unless --no-start,
# used by rollback.sh). Record the first restore drill in docs/DEPLOYS.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

DAY=""
START=1
while (($#)); do
  case "$1" in
    --no-start) START=0; shift ;;
    -h | --help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) DAY="$1"; shift ;;
  esac
done
[[ "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "usage: restore.sh <YYYY-MM-DD> [--no-start]"

require_root
take_lock 3600

NAME="ratchet-${DAY}.tar.gz"
ARCHIVE="$RATCHET_BACKUP_DIR/$NAME"

fetch_remote() {
  local bucket prefix
  bucket="$(env_get RATCHET_BACKUP_S3_BUCKET "$(env_get NEBIUS_S3_BUCKET)")"
  prefix="$(env_get RATCHET_BACKUP_S3_PREFIX backups/ratchet)"
  if [[ -z "$bucket" ]] || ! command -v aws >/dev/null; then
    die "$ARCHIVE not found and no object storage configured"
  fi
  log "downloading s3://${bucket}/${prefix%/}/$NAME"
  local f
  for f in "$NAME" "$NAME.sha256"; do
    AWS_ACCESS_KEY_ID="$(env_get NEBIUS_S3_ACCESS_KEY_ID)" \
      AWS_SECRET_ACCESS_KEY="$(env_get NEBIUS_S3_SECRET_ACCESS_KEY)" \
      AWS_DEFAULT_REGION="$(env_get NEBIUS_REGION eu-north1)" \
      aws --endpoint-url "$(env_get NEBIUS_S3_ENDPOINT)" s3 cp --only-show-errors \
      "s3://${bucket}/${prefix%/}/$f" "$RATCHET_BACKUP_DIR/$f"
  done
}

main() {
  local stamp aside staging
  [[ -f "$ARCHIVE" ]] || fetch_remote
  if [[ -f "$ARCHIVE.sha256" ]]; then
    (cd "$RATCHET_BACKUP_DIR" && sha256sum -c --quiet "$NAME.sha256") || die "checksum mismatch for $ARCHIVE"
  else
    log "WARNING: no $NAME.sha256; restoring unverified"
  fi

  staging="$(mktemp -d "$RATCHET_SRV_DIR/.restore.XXXXXX")"
  tar --extract --gzip --file "$ARCHIVE" -C "$staging"
  [[ -d "$staging/data" ]] || die "$ARCHIVE has no data/"

  log "stopping $RATCHET_UNIT"
  systemctl stop "$RATCHET_UNIT"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  aside="$RATCHET_SRV_DIR/pre-restore-$stamp"
  mkdir -p "$aside"
  for dir in data skills; do
    if [[ -e "$RATCHET_SRV_DIR/$dir" ]]; then
      mv "$RATCHET_SRV_DIR/$dir" "$aside/$dir"
    fi
    if [[ -e "$staging/$dir" ]]; then
      mv "$staging/$dir" "$RATCHET_SRV_DIR/$dir"
    fi
  done
  if [[ -d "$staging/sqlite-snapshots" ]]; then
    while IFS= read -r -d '' snap; do
      rel="${snap#"$staging/sqlite-snapshots/"}"
      mkdir -p "$RATCHET_SRV_DIR/$(dirname "$rel")"
      mv "$snap" "$RATCHET_SRV_DIR/${rel%.snapshot}"
    done < <(find "$staging/sqlite-snapshots" -name '*.db.snapshot' -print0)
  fi
  rm -rf "$staging"
  mkdir -p "$RATCHET_SRV_DIR/data/sessions" "$RATCHET_SRV_DIR/tmp"
  chown -R "$RATCHET_USER:$RATCHET_USER" "$RATCHET_SRV_DIR/data" "$RATCHET_SRV_DIR/tmp"
  [[ -d "$RATCHET_SRV_DIR/skills" ]] && chown -R "$RATCHET_USER:$RATCHET_USER" "$RATCHET_SRV_DIR/skills"
  log "previous state kept in $aside (delete it once the restore is confirmed)"

  ledger_append restore "$(current_sha)" ok "backup=$DAY"
  deploys_md_sync
  if ((START)); then
    systemctl start "$RATCHET_UNIT"
    wait_healthy "$RATCHET_LOCAL_URL" 120 || die "not healthy after restore; journalctl -u $RATCHET_UNIT"
  fi
  notify "Ratchet restored backup $DAY on $(hostname)"
  log "restore of $DAY done"
}

main
