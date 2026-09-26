#!/bin/sh
set -eu

PROJECT_DIR="${1:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
RUNTIME_DIR="$PROJECT_DIR/adapter-runtime/1688-shopkeeper"
UPSTREAM_COMMIT="99537bce780dd2bb926891f11223aeaa30842eff"
DOWNLOAD_URL="https://github.com/next-1688/1688-shopkeeper/archive/$UPSTREAM_COMMIT.tar.gz"
EXPECTED_SHA256="96bfd38a6f64b3cc5c65fa74ed1e4f7d96edff84fc7b43737cde84fbd861e5a3"
SOURCE_MARKER="$RUNTIME_DIR/.autofish-source"

if [ -f "$SOURCE_MARKER" ] && [ "$(sed -n '1p' "$SOURCE_MARKER")" = "$UPSTREAM_COMMIT" ]; then
  printf '%s\n' "1688-shopkeeper 1.0.1 ($UPSTREAM_COMMIT) 已安装"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
ARCHIVE="$TMP_DIR/1688-shopkeeper.tar.gz"
STAGE_DIR="$TMP_DIR/stage"

curl --fail --silent --show-error --location "$DOWNLOAD_URL" --output "$ARCHIVE"
ACTUAL_SHA256="$(openssl dgst -sha256 "$ARCHIVE" | awk '{print $NF}')"
if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
  printf '%s\n' "1688-shopkeeper 下载包校验失败" >&2
  exit 1
fi

mkdir -p "$STAGE_DIR"
python3 - "$ARCHIVE" "$STAGE_DIR" "$UPSTREAM_COMMIT" <<'PY'
import sys
import tarfile
from pathlib import Path

archive, target, commit = sys.argv[1:]
expected_root = f"1688-shopkeeper-{commit}"
target_path = Path(target).resolve()
with tarfile.open(archive, "r:gz") as package:
    members = package.getmembers()
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
            raise SystemExit("归档包含不安全路径")
        if not (member.isfile() or member.isdir()):
            raise SystemExit("归档包含不允许的特殊文件")
    package.extractall(target_path, members=members)
PY

EXTRACTED_DIR="$STAGE_DIR/1688-shopkeeper-$UPSTREAM_COMMIT"
test -f "$EXTRACTED_DIR/cli.py"
test -f "$EXTRACTED_DIR/requirements.txt"
printf '%s\n' "$UPSTREAM_COMMIT" > "$EXTRACTED_DIR/.autofish-source"

if [ -e "$RUNTIME_DIR" ]; then
  BACKUP_DIR="$PROJECT_DIR/adapter-runtime/1688-shopkeeper.previous.$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$RUNTIME_DIR" "$BACKUP_DIR"
  printf '%s\n' "旧运行时已保留在 $BACKUP_DIR"
fi
mv "$EXTRACTED_DIR" "$RUNTIME_DIR"
chmod 0755 "$RUNTIME_DIR/cli.py"
printf '%s\n' "1688-shopkeeper 1.0.1 已安装并通过 SHA-256 校验"
