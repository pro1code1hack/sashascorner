#!/usr/bin/env bash
# Nightly off-box backup of the live SQLite database.
#
# A `cp` of a database file in WAL mode is not a backup -- the WAL segment can be
# mid-checkpoint and a raw copy can land you a corrupt or torn file with no warning
# until the day you need it. This uses SQLite's own *online backup API* via the
# `sqlite3` CLI's `.backup` dot-command, which takes a consistent snapshot while the
# app keeps writing, then verifies the COPY (never the live file -- that would just
# prove the app itself still works) opens and passes `PRAGMA integrity_check` before
# it is trusted, gzipped, rotated and (optionally) shipped off-box.
#
# A backup nobody has restored is a hope, not a backup -- see restore.sh, and
# docs/OPERATIONS.md for the proof this was actually run once end to end.
#
# Env (all optional except CAFEOPS_DB_PATH, which has no safe default):
#   CAFEOPS_DB_PATH            path to the live .db file (NOT a sqlite+pysqlite:// URL)
#   CAFEOPS_BACKUP_DIR         where rotated backups live          [default: ./backups]
#   CAFEOPS_BACKUP_RETAIN_DAYS local copies kept before pruning     [default: 14]
#   CAFEOPS_MEDIA_DIR          menu photo directory, shipped with the database when
#                               REMOTE is set (files are content-addressed, so rsync
#                               only ever adds)                      [default: unset]
#   CAFEOPS_BACKUP_REMOTE      rsync destination, e.g. user@host:/srv/cafeops-backups
#                               (needs an SSH key already set up -- this script carries
#                               no credential). Unset means LOCAL ONLY and this script
#                               says so loudly, because a backup that never leaves the
#                               box does not survive the box.
#
# Exit non-zero on ANY failure, including a failed integrity check -- systemd and
# `docker compose logs` both surface a non-zero exit; a script that limps on and
# exits 0 after a corrupt backup is worse than one that never ran.

set -euo pipefail

DB_PATH="${CAFEOPS_DB_PATH:?set CAFEOPS_DB_PATH to the live .db file, e.g. /data/cafeops.db}"
BACKUP_DIR="${CAFEOPS_BACKUP_DIR:-./backups}"
RETAIN_DAYS="${CAFEOPS_BACKUP_RETAIN_DAYS:-14}"
REMOTE="${CAFEOPS_BACKUP_REMOTE:-}"

if [ ! -f "$DB_PATH" ]; then
    echo "BACKUP FAILED: no database at $DB_PATH" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="$BACKUP_DIR/.tmp-$ts.db"
dest="$BACKUP_DIR/cafeops-$ts.db"

cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

echo "[$ts] backing up $DB_PATH -> $tmp (sqlite3 .backup, online/WAL-safe)"
sqlite3 "$DB_PATH" ".backup '$tmp'"

echo "[$ts] verifying the COPY: PRAGMA integrity_check"
integrity="$(sqlite3 "$tmp" 'PRAGMA integrity_check;')"
if [ "$integrity" != "ok" ]; then
    echo "BACKUP FAILED: integrity check on the copy returned: $integrity" >&2
    exit 1
fi

echo "[$ts] verifying the copy actually opens and answers a real query"
table_count="$(sqlite3 "$tmp" "SELECT count(*) FROM sqlite_master WHERE type='table';")"
if [ "${table_count:-0}" -lt 1 ]; then
    echo "BACKUP FAILED: copy opened but has no tables ($table_count)" >&2
    exit 1
fi
echo "[$ts] ok: integrity_check=ok, $table_count tables present"

mv "$tmp" "$dest"
trap - EXIT
gzip -f "$dest"
dest="$dest.gz"
size="$(du -h "$dest" | cut -f1)"
echo "[$ts] backup written: $dest ($size)"

if [ -n "$REMOTE" ]; then
    echo "[$ts] shipping off-box to $REMOTE"
    rsync -a "$dest" "$REMOTE/"
    echo "[$ts] off-box copy confirmed at $REMOTE"
    if [ -n "${CAFEOPS_MEDIA_DIR:-}" ] && [ -d "$CAFEOPS_MEDIA_DIR" ]; then
        rsync -a "$CAFEOPS_MEDIA_DIR/" "$REMOTE/media/"
        echo "[$ts] menu photos shipped to $REMOTE/media/"
    fi
else
    echo "[$ts] WARNING: CAFEOPS_BACKUP_REMOTE is not set. This backup is LOCAL ONLY," >&2
    echo "           on the same disk as the database it protects. Set CAFEOPS_BACKUP_REMOTE" >&2
    echo "           to an off-box rsync target (with a working SSH key) before this is a" >&2
    echo "           real backup rather than a second copy of the same failure mode." >&2
fi

if [ "${RETAIN_DAYS}" -gt 0 ]; then
    pruned="$(find "$BACKUP_DIR" -maxdepth 1 -name 'cafeops-*.db.gz' -mtime "+${RETAIN_DAYS}" -print -delete | wc -l | tr -d ' ')"
    if [ "$pruned" -gt 0 ]; then
        echo "[$ts] pruned $pruned local backup(s) older than ${RETAIN_DAYS}d"
    fi
fi

echo "[$ts] done"
