#!/usr/bin/env bash
# Turns backup.sh into a long-running "nightly" job without adding cron to the image.
# Only used by the `backup` service in docker-compose.yml -- the systemd path
# (deploy/systemd/cafeops-backup.timer) uses the OS's own timer instead, which is the
# more standard tool for this and the reason it stays the primary documented path.
#
# Sleeps until the next CAFEOPS_BACKUP_TIME (UTC, HH:MM, default 02:00), runs
# backup.sh once, then repeats. A non-zero exit from backup.sh is logged and the loop
# continues rather than exiting -- a crashed backup container that silently stops
# backing up forever is worse than one that tries again tomorrow and is visible in
# `docker compose ps` either way (the exit code shows in the logs, and `docker compose
# logs backup` is the first place to look if a night's backup is missing).

set -uo pipefail

TARGET_TIME="${CAFEOPS_BACKUP_TIME:-02:00}"

echo "cafeops backup loop starting; daily target ${TARGET_TIME} UTC"

while true; do
    now_epoch="$(date -u +%s)"
    target_epoch="$(date -u -d "today ${TARGET_TIME}" +%s)"
    if [ "$target_epoch" -le "$now_epoch" ]; then
        target_epoch="$(date -u -d "tomorrow ${TARGET_TIME}" +%s)"
    fi
    sleep_secs=$((target_epoch - now_epoch))
    echo "next backup at $(date -u -d "@${target_epoch}" '+%Y-%m-%d %H:%M UTC') (sleeping ${sleep_secs}s)"
    sleep "$sleep_secs"

    if ! /app/deploy/backup.sh; then
        echo "backup.sh exited non-zero -- see the output above. Will try again tomorrow." >&2
    fi
    # Clear the target minute so a slow backup.sh run can't cause an immediate re-fire.
    sleep 61
done
