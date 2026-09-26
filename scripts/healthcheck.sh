#!/bin/sh
set -eu

frontend_url="${AUTOFISH_FRONTEND_URL:-http://127.0.0.1:18180/login}"
backend_url="${AUTOFISH_BACKEND_URL:-http://127.0.0.1:18181}"

curl --connect-timeout 3 --max-time 10 --fail --silent --show-error "${backend_url}/health" >/dev/null
curl --connect-timeout 3 --max-time 10 --fail --silent --show-error "${backend_url}/ready" >/dev/null
curl --connect-timeout 3 --max-time 10 --fail --silent --show-error "${frontend_url}" >/dev/null

echo "AutoFish healthcheck passed"
