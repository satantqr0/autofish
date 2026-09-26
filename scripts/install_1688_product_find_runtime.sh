#!/bin/sh
set -eu

PROJECT_DIR="${1:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
RUNTIME_DIR="$PROJECT_DIR/adapter-runtime/1688-product-find-safe"
UPSTREAM_DIR="$RUNTIME_DIR/upstream/1688-product-find-1.7.0"
DOWNLOAD_URL="https://clawhub.ai/api/v1/download?slug=1688-product-find&ownerHandle=1688aiinfra&version=1.7.0"
EXPECTED_SHA256="1472768279b340657f8c3c220350a89b54ec4c482d4b1f654c8a646d1af1a4d5"

if [ -f "$UPSTREAM_DIR/_meta.json" ]; then
  printf '%s\n' "1688-product-find 1.7.0 已安装"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
ARCHIVE="$TMP_DIR/1688-product-find-1.7.0.zip"

curl --fail --silent --show-error --location "$DOWNLOAD_URL" --output "$ARCHIVE"
ACTUAL_SHA256="$(openssl dgst -sha256 "$ARCHIVE" | awk '{print $NF}')"
if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
  printf '%s\n' "1688-product-find 下载包校验失败" >&2
  exit 1
fi

mkdir -p "$UPSTREAM_DIR"
python3 - "$ARCHIVE" "$UPSTREAM_DIR" <<'PY'
import sys
import zipfile

archive, target = sys.argv[1:]
with zipfile.ZipFile(archive) as package:
    for member in package.infolist():
        path = member.filename.replace("\\", "/")
        if path.startswith("/") or ".." in path.split("/"):
            raise SystemExit("归档包含不安全路径")
    package.extractall(target)
PY

chmod 0755 "$RUNTIME_DIR/cli.py"
printf '%s\n' "1688-product-find 1.7.0 安装并校验完成"
