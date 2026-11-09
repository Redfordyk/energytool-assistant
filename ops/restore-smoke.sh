#!/bin/sh
set -eu

ROOT=/opt/energytool-assistant
RELEASE="$ROOT/current"
BACKUP_DIR="$ROOT/backups"
WORKDIR=$(mktemp -d /tmp/energytool-restore.XXXXXX)
TEST_DB="energytool_restore_$(date -u +%Y%m%d%H%M%S)_$$"
DB_CREATED=0

compose() {
    docker compose \
        --project-name energytool \
        --project-directory "$RELEASE" \
        --env-file "$ROOT/.env" \
        -f "$RELEASE/compose.yaml" "$@"
}

cleanup() {
    if [ "$DB_CREATED" -eq 1 ]; then
        compose exec -T db dropdb --if-exists -U energytool "$TEST_DB" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$WORKDIR"
}
trap cleanup EXIT HUP INT TERM

backup=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'energytool-*.tar.gz' -printf '%T@ %p\n' \
    | sort -nr | sed -n '1s/^[^ ]* //p')
test -n "$backup"
tar -xzf "$backup" -C "$WORKDIR"
test -s "$WORKDIR/database.dump"
compose exec -T db pg_restore --list < "$WORKDIR/database.dump" >/dev/null

archive_count=0
for archive in "$WORKDIR"/knowledge/*.zip; do
    test -f "$archive" || continue
    expected=$(basename "$archive" .zip)
    actual=$(sha256sum "$archive" | cut -d' ' -f1)
    test "$actual" = "$expected"
    archive_count=$((archive_count + 1))
done
test "$archive_count" -gt 0

compose exec -T db createdb -U energytool "$TEST_DB"
DB_CREATED=1
compose exec -T db pg_restore --no-owner --no-privileges -U energytool -d "$TEST_DB" \
    < "$WORKDIR/database.dump"
schema_rows=$(compose exec -T db psql -XAt -U energytool -d "$TEST_DB" \
    -c "SELECT count(*) FROM alembic_version")
test "$schema_rows" -eq 1
active_revision=$(compose exec -T db psql -XAt -U energytool -d "$TEST_DB" \
    -c "SELECT value FROM application_settings WHERE key='active_knowledge'")
test -s "$WORKDIR/knowledge/$active_revision.zip"
printf 'Restore validation passed: %s; schema rows=%s; knowledge archives=%s\n' \
    "$(basename "$backup")" "$schema_rows" "$archive_count"
