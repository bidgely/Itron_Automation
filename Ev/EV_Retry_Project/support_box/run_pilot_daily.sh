#!/bin/bash

set -euo pipefail

PROJECT_ROOT="/home/bsup/itron-automation"
DATE_STR="$(date +%Y%m%d)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="$PROJECT_ROOT/support_box/daily_env.sh"
LOG_DIR="$PROJECT_ROOT/logs"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --pilot <configured-pilot-key> [--date YYYYMMDD]
EOF
}

PILOT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --pilot)
      PILOT="${2:-}"
      shift 2
      ;;
    --date)
      DATE_STR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [ -z "$PILOT" ]; then
  echo "Missing required --pilot."
  usage
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE"
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Missing Python interpreter: $PYTHON_BIN"
  exit 1
fi

source "$ENV_FILE"

mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/${PILOT}_daily_${DATE_STR}.log"

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN is not set. Export it before running daily PR automation."
  exit 1
fi

prepare_utils_repo() {
  if [ -z "${ITRON_UTILS_REPO_PATH:-}" ]; then
    echo "ITRON_UTILS_REPO_PATH is not set."
    exit 1
  fi

  if [ ! -d "$ITRON_UTILS_REPO_PATH/.git" ]; then
    echo "Utils automation repo is missing or invalid: $ITRON_UTILS_REPO_PATH"
    exit 1
  fi

  git -C "$ITRON_UTILS_REPO_PATH" reset --hard origin/master
  git -C "$ITRON_UTILS_REPO_PATH" clean -fd
}

notify_gchat() {
  local message="$1"

  if [ -z "${GCHAT_WEBHOOK_URL:-}" ]; then
    echo "GCHAT_WEBHOOK_URL is not set. Skipping Google Chat notification."
    return 0
  fi

  GCHAT_MESSAGE="$message" python3 - <<'PY'
import json
import os
import urllib.request

url = os.environ["GCHAT_WEBHOOK_URL"]
payload = {"text": os.environ["GCHAT_MESSAGE"]}
data = json.dumps(payload).encode("utf-8")
request = urllib.request.Request(
    url,
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    response.read()
PY
}

cleanup() {
  if [ -n "${TUNNEL_PID:-}" ] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
    kill "$TUNNEL_PID" >/dev/null 2>&1 || true
    wait "$TUNNEL_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT

# Detect if pilot is UAT and open the appropriate tunnel
IS_UAT=$("$PYTHON_BIN" - "$PILOT" <<'PY'
import sys
from app.pilots import get_pilot_definition
pilot = get_pilot_definition(sys.argv[1])
print("true" if pilot.uat else "false")
PY
)

if [ "$IS_UAT" = "true" ]; then
  TUNNEL_KEY="${ITRON_UAT_TUNNEL_KEY:-$ITRON_TUNNEL_KEY}"
  TUNNEL_HOST="${ITRON_UAT_TUNNEL_HOST:-$ITRON_TUNNEL_HOST}"
  TUNNEL_PORT="${ITRON_UAT_TUNNEL_PORT:-3311}"
  TUNNEL_TARGET="${ITRON_UAT_TUNNEL_TARGET:-$ITRON_TUNNEL_TARGET}"
else
  TUNNEL_KEY="$ITRON_TUNNEL_KEY"
  TUNNEL_HOST="$ITRON_TUNNEL_HOST"
  TUNNEL_PORT="$ITRON_TUNNEL_PORT"
  TUNNEL_TARGET="$ITRON_TUNNEL_TARGET"
fi

ssh -i "$TUNNEL_KEY" \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=3 \
  -L "${TUNNEL_PORT}:${TUNNEL_TARGET}" \
  "$TUNNEL_HOST" \
  -N &
TUNNEL_PID=$!

sleep 3

cd "$PROJECT_ROOT"
prepare_utils_repo
if OUTPUT=$("$PYTHON_BIN" -m app run-pilot-flow-and-create-pr \
  --pilot "$PILOT" \
  --date "$DATE_STR" \
  --output-dir output \
  --create-pr 2>&1); then
  printf "%s\n" "$OUTPUT" | tee "$RUN_LOG"
  MESSAGE=$(printf "%s" "$OUTPUT" | "$PYTHON_BIN" "$PROJECT_ROOT/support_box/build_gchat_message.py" \
    --pilot "$PILOT" \
    --date "$DATE_STR" \
    --status success \
    --mode daily)
  notify_gchat "$MESSAGE"
else
  STATUS=$?
  printf "%s\n" "$OUTPUT" | tee "$RUN_LOG"
  MESSAGE=$(printf "%s" "$OUTPUT" | "$PYTHON_BIN" "$PROJECT_ROOT/support_box/build_gchat_message.py" \
    --pilot "$PILOT" \
    --date "$DATE_STR" \
    --status failure \
    --mode daily \
    --run-log "$RUN_LOG")
  notify_gchat "$MESSAGE"
  exit "$STATUS"
fi
