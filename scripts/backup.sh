#!/bin/sh
set -eu
umask 077

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
project_dir="${AUTOFISH_PROJECT_DIR:-$(dirname "$script_dir")}"
backup_dir="${AUTOFISH_BACKUP_DIR:-${project_dir}/backups}"
timestamp="$(date '+%Y%m%d-%H%M%S')"
target="${backup_dir}/autofish-${timestamp}.sql.gz"
temporary_sql="${backup_dir}/.autofish-${timestamp}.sql.tmp"
temporary_gzip="${target}.tmp"
checksum="${target}.sha256"
temporary_checksum="${checksum}.tmp"

docker_bin="${AUTOFISH_DOCKER_BIN:-}"
if [ -z "$docker_bin" ]; then
  if command -v docker >/dev/null 2>&1; then
    docker_bin="$(command -v docker)"
  elif [ -x /Volume2/@apps/DockerEngine/dockerd/bin/docker ]; then
    docker_bin="/Volume2/@apps/DockerEngine/dockerd/bin/docker"
  else
    echo "Docker CLI not found; set AUTOFISH_DOCKER_BIN" >&2
    exit 1
  fi
fi

if [ ! -x "$docker_bin" ]; then
  echo "Docker CLI is not executable: $docker_bin" >&2
  exit 1
fi

cleanup() {
  rm -f "$temporary_sql" "$temporary_gzip" "$temporary_checksum"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$backup_dir"
chmod 0700 "$backup_dir"
cd "$project_dir"

"$docker_bin" compose exec -T postgres sh -c \
  'pg_dump --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  >"$temporary_sql"

test -s "$temporary_sql"
gzip -c "$temporary_sql" >"$temporary_gzip"
gzip -t "$temporary_gzip"
test -s "$temporary_gzip"
digest="$(sha256sum "$temporary_gzip" | cut -d ' ' -f 1)"
printf '%s  %s\n' "$digest" "$(basename "$target")" >"$temporary_checksum"
chmod 0600 "$temporary_gzip" "$temporary_checksum"
mv "$temporary_checksum" "$checksum"
mv "$temporary_gzip" "$target"

find "$backup_dir" -type f -name 'autofish-*.sql.gz' -mtime +14 -delete
find "$backup_dir" -type f -name 'autofish-*.sql.gz.sha256' -mtime +14 -delete
echo "Backup written to $target"
