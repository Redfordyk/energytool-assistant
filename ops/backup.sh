#!/bin/sh
set -eu

ROOT=/opt/energytool-assistant
RELEASE="$ROOT/current"
BACKUP_DIR="$ROOT/backups"
umask 077
install -d -m 700 "$BACKUP_DIR"

compose() {
    docker compose \
        --project-name energytool \
        --project-directory "$RELEASE" \
        --env-file "$ROOT/.env" \
        -f "$RELEASE/compose.yaml" "$@"
}

timestamp=$(date -u +%Y%m%dT%H%M%S%NZ)
workdir=$(mktemp -d "$BACKUP_DIR/.energytool-${timestamp}.XXXXXX")
archive="$BACKUP_DIR/energytool-${timestamp}.tar.gz"
cleanup() {
    rm -rf -- "$workdir"
}
trap cleanup EXIT HUP INT TERM
install -d -m 700 "$workdir/knowledge"

compose exec -T db pg_dump -Fc -U energytool -d energytool > "$workdir/database.dump"
test -s "$workdir/database.dump"
app_id=$(compose ps -q app)
test -n "$app_id"
docker cp "$app_id:/knowledge/." "$workdir/knowledge/" >/dev/null
test -n "$(find "$workdir/knowledge" -maxdepth 1 -type f -name '*.zip' -print -quit)"
compose exec -T db pg_restore --list < "$workdir/database.dump" >/dev/null

tar -C "$workdir" -czf "$workdir/bundle.tar.gz" database.dump knowledge
tar -tzf "$workdir/bundle.tar.gz" >/dev/null
chmod 600 "$workdir/bundle.tar.gz"
mv "$workdir/bundle.tar.gz" "$archive"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'energytool-*.tar.gz' -mtime +14 -delete
printf 'Backup created and validated: %s\n' "$(basename "$archive")"
