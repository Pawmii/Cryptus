#!/bin/sh
# Back up the database and the secrets that decrypt it, keeping the last N days.
#
#   sudo sh deploy/backup.sh                       # one-off
#   echo "17 3 * * * root sh /opt/cryptus/deploy/backup.sh" > /etc/cron.d/cryptus-backup
#
# Copy the backup directory off this server (rsync, restic, object storage). Restore: see docs/PRODUCTION.md.
set -eu
cd "$(dirname "$0")"
DEST="${CRYPTUS_BACKUP_DIR:-/var/backups/cryptus}"
KEEP_DAYS="${CRYPTUS_BACKUP_KEEP_DAYS:-14}"
TS=$(date -u +%Y%m%dT%H%M%SZ)

umask 077
mkdir -p "$DEST"
docker compose -f docker-compose.yml --env-file .env exec -T postgres \
  pg_dump -U cryptus -d cryptus --format=custom --no-owner > "$DEST/cryptus-$TS.dump.partial"
mv "$DEST/cryptus-$TS.dump.partial" "$DEST/cryptus-$TS.dump"
tar -czf "$DEST/config-$TS.tgz" .env secrets
find "$DEST" -maxdepth 1 \( -name 'cryptus-*.dump' -o -name 'config-*.tgz' \) -mtime +"$KEEP_DAYS" -delete
echo "backup written: $DEST/cryptus-$TS.dump, $DEST/config-$TS.tgz"
