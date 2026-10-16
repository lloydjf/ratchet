#!/usr/bin/env bash
# Nightly backup of /srv/ratchet/{data,skills}; run by ratchet-backup.timer
# at 03:00 UTC, or by hand: sudo /opt/ratchet/deploy/vm/backup.sh
#
# - SQLite files are copied with the online backup API (consistent under WAL), not tar'd live.
# - Excluded: data/sessions (Simulate scratch, auto-reset), tmp/, the PII key (kept apart from
#   the encrypted map on purpose) and /etc/ratchet/env (secrets never leave the VM).
# - Output: /var/backups/ratchet/ratchet-YYYY-MM-DD.tar.gz + .sha256 (a re-run the same day
#   replaces that day's file). Upload to Nebius Object Storage with `aws s3 cp
#   --endpoint-url $NEBIUS_S3_ENDPOINT` when RATCHET_BACKUP_S3_BUCKET (or NEBIUS_S3_BUCKET)
#   and the S3 access key are configured.
# - Retention: RATCHET_BACKUP_RETENTION_DAYS (default 14), locally and in the bucket.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_root
take_lock 3600
trap 'notify "Ratchet backup FAILED on $(hostname); see journalctl -u ratchet-backup"' ERR

DAY="$(date -u +%F)"
NAME="ratchet-${DAY}.tar.gz"
RETENTION_DAYS="$(env_get RATCHET_BACKUP_RETENTION_DAYS 14)"
BUCKET="$(env_get RATCHET_BACKUP_S3_BUCKET "$(env_get NEBIUS_S3_BUCKET)")"
PREFIX="$(env_get RATCHET_BACKUP_S3_PREFIX backups/ratchet)"
PREFIX="${PREFIX%/}"

s3() {
  AWS_ACCESS_KEY_ID="$(env_get NEBIUS_S3_ACCESS_KEY_ID)" \
    AWS_SECRET_ACCESS_KEY="$(env_get NEBIUS_S3_SECRET_ACCESS_KEY)" \
    AWS_DEFAULT_REGION="$(env_get NEBIUS_REGION eu-north1)" \
    aws --endpoint-url "$(env_get NEBIUS_S3_ENDPOINT)" s3 "$@"
}

s3_configured() {
  [[ -n "$BUCKET" && -n "$(env_get NEBIUS_S3_ENDPOINT)" && -n "$(env_get NEBIUS_S3_ACCESS_KEY_ID)" ]] \
    && command -v aws >/dev/null
}

snapshot_sqlite() {
  local src="$1" dst="$2"
  python3 - "$src" "$dst" <<'PY'
import pathlib, sqlite3, sys
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
for db in sorted(src.rglob("*.db")):
    with db.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            continue
    out = dst / db.relative_to(src.parent).with_suffix(".db.snapshot")
    out.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as source, sqlite3.connect(out) as target:
        source.backup(target)
    print(out.relative_to(dst))
PY
}

cutoff_day() { date -u -d "-${RETENTION_DAYS} days" +%F; }

prune_local() {
  local cutoff file day
  cutoff="$(cutoff_day)"
  for file in "$RATCHET_BACKUP_DIR"/ratchet-*.tar.gz; do
    [[ -e "$file" ]] || continue
    day="${file##*/ratchet-}"
    day="${day%.tar.gz}"
    if [[ "$day" < "$cutoff" ]]; then
      log "retention: deleting $file"
      rm -f "$file" "$file.sha256"
    fi
  done
}

prune_remote() {
  local cutoff object day
  cutoff="$(cutoff_day)"
  s3 ls "s3://${BUCKET}/${PREFIX}/" | awk '{print $4}' | while read -r object; do
    [[ "$object" =~ ^ratchet-([0-9]{4}-[0-9]{2}-[0-9]{2})\.tar\.gz(\.sha256)?$ ]] || continue
    day="${BASH_REMATCH[1]}"
    if [[ "$day" < "$cutoff" ]]; then
      log "retention: deleting s3://${BUCKET}/${PREFIX}/${object}"
      s3 rm --only-show-errors "s3://${BUCKET}/${PREFIX}/${object}"
    fi
  done
}

main() {
  local work members=()
  install -d -m 0700 "$RATCHET_BACKUP_DIR"
  work="$(mktemp -d "$RATCHET_BACKUP_DIR/.work.XXXXXX")"
  trap 'rm -rf "$work"' EXIT
  [[ -d "$RATCHET_SRV_DIR/data" ]] && members+=(data)
  [[ -d "$RATCHET_SRV_DIR/skills" ]] && members+=(skills)
  ((${#members[@]})) || die "nothing to back up under $RATCHET_SRV_DIR"

  log "sqlite snapshots"
  mkdir -p "$work/sqlite-snapshots"
  snapshot_sqlite "$RATCHET_SRV_DIR/data" "$work/sqlite-snapshots" | sed 's/^/  /' >&2

  log "archiving ${members[*]} -> $NAME"
  tar --create --gzip --file "$work/$NAME" \
    --exclude='data/sessions' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='*.db-journal' --exclude='*.key' \
    -C "$RATCHET_SRV_DIR" "${members[@]}" \
    -C "$work" sqlite-snapshots
  (cd "$work" && sha256sum "$NAME" >"$NAME.sha256")
  mv -f "$work/$NAME" "$work/$NAME.sha256" "$RATCHET_BACKUP_DIR/"
  log "wrote $RATCHET_BACKUP_DIR/$NAME ($(du -h "$RATCHET_BACKUP_DIR/$NAME" | cut -f1))"

  if s3_configured; then
    log "uploading to s3://${BUCKET}/${PREFIX}/"
    s3 cp --only-show-errors "$RATCHET_BACKUP_DIR/$NAME" "s3://${BUCKET}/${PREFIX}/$NAME"
    s3 cp --only-show-errors "$RATCHET_BACKUP_DIR/$NAME.sha256" "s3://${BUCKET}/${PREFIX}/$NAME.sha256"
    prune_remote
  else
    log "object storage not configured (bucket/endpoint/key/aws); local copy only"
  fi
  prune_local
  log "backup $DAY done"
}

main
