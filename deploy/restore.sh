#!/usr/bin/env bash
# Restore a backup produced by backup.sh. Interactive by design: this overwrites the
# live database, and a restore run by accident is exactly the kind of mistake a nightly
# backup exists to recover FROM, not cause.
#
# Usage:
#   deploy/restore.sh <backup-file.db|backup-file.db.gz> [target-db-path]
#
# target-db-path defaults to $CAFEOPS_DB_PATH. The backup is verified (integrity_check
# on the file being restored, not just trusted because it came from backup.sh) BEFORE
# anything is asked or overwritten. The current live file, if any, is copied aside
# rather than deleted, so a bad restore is itself recoverable.
#
# This script does not stop cafeops-api/-bot/-scheduler for you -- do that first
# (`systemctl stop cafeops-api cafeops-bot cafeops-scheduler` or
# `docker compose stop api bot scheduler`). Restoring under a live writer defeats the
# point of verifying the file first.

set -euo pipefail

BACKUP_FILE="${1:?usage: restore.sh <backup-file.db[.gz]> [target-db-path]}"
TARGET="${2:-${CAFEOPS_DB_PATH:-}}"

if [ -z "$TARGET" ]; then
    echo "no target: pass it as \$2, or set CAFEOPS_DB_PATH" >&2
    exit 1
fi
if [ ! -f "$BACKUP_FILE" ]; then
    echo "no such backup file: $BACKUP_FILE" >&2
    exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
candidate="$work/candidate.db"

case "$BACKUP_FILE" in
    *.gz) gunzip -c "$BACKUP_FILE" > "$candidate" ;;
    *) cp "$BACKUP_FILE" "$candidate" ;;
esac

echo "verifying $BACKUP_FILE before touching anything"
integrity="$(sqlite3 "$candidate" 'PRAGMA integrity_check;')"
if [ "$integrity" != "ok" ]; then
    echo "RESTORE ABORTED: integrity check failed: $integrity" >&2
    exit 1
fi
table_count="$(sqlite3 "$candidate" "SELECT count(*) FROM sqlite_master WHERE type='table';")"
if [ "${table_count:-0}" -lt 1 ]; then
    echo "RESTORE ABORTED: candidate has no tables" >&2
    exit 1
fi
echo "candidate verified: integrity_check=ok, $table_count tables"

if [ "${CAFEOPS_RESTORE_YES:-}" != "1" ]; then
    read -r -p "This will OVERWRITE $TARGET with $BACKUP_FILE. Type YES to continue: " confirm
    if [ "$confirm" != "YES" ]; then
        echo "aborted, nothing touched"
        exit 1
    fi
fi

ts="$(date -u +%Y%m%dT%H%M%SZ)"
if [ -f "$TARGET" ]; then
    pre="$TARGET.pre-restore-$ts"
    cp "$TARGET" "$pre"
    echo "existing $TARGET saved as $pre before overwrite"
fi

mkdir -p "$(dirname "$TARGET")"
cp "$candidate" "$TARGET"
# Drop any stale WAL/SHM sidecars from the file we just replaced -- they belong to the
# OLD database's write-ahead log, not the one just restored, and leaving them would let
# SQLite try to replay unrelated frames against the new file on next open.
rm -f "${TARGET}-wal" "${TARGET}-shm"

echo "restored $TARGET from $BACKUP_FILE"
echo "verifying the RESTORED file opens with the same check"
sqlite3 "$TARGET" 'PRAGMA integrity_check;'
echo "done. Restart cafeops-api / cafeops-bot / cafeops-scheduler (or the docker compose services) now."
