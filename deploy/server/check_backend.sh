#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://127.0.0.1:8000}"

request_code() {
  local method="$1"
  local path="$2"
  shift 2
  local args=(
    --silent --show-error --output /dev/null
    --write-out '%{http_code}'
    --request "$method"
  )
  if [[ "$method" != "GET" ]]; then
    args+=(--header 'Content-Type: application/json' --data '{}')
  fi
  curl "${args[@]}" "$@" "${base_url}${path}"
}

health_body="$(curl --fail --silent --show-error "${base_url}/health")"
if [[ "$health_body" != *'"status":"ok"'* && "$health_body" != *'"status": "ok"'* ]]; then
  echo "FAIL: unexpected /health response" >&2
  exit 1
fi

detection_code="$(request_code POST /api/detection \
  --header 'X-GZZ-Protocol-Version: 1' \
  --header 'Idempotency-Key: 00000000-0000-4000-8000-000000000001')"
heartbeat_code="$(request_code POST /api/heartbeat)"
dashboard_code="$(request_code GET /api/dashboard/overview)"

if [[ "$detection_code" != "401" ]]; then
  echo "FAIL: unauthenticated detection request returned ${detection_code}, expected 401" >&2
  exit 1
fi
if [[ "$heartbeat_code" != "401" ]]; then
  echo "FAIL: unauthenticated heartbeat request returned ${heartbeat_code}, expected 401" >&2
  exit 1
fi
if [[ "$dashboard_code" != "401" ]]; then
  echo "FAIL: unauthenticated dashboard request returned ${dashboard_code}, expected 401" >&2
  exit 1
fi

echo "PASS: health is ready and protected routes reject missing credentials"
