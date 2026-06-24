#!/bin/bash

set -euo pipefail

PROJECT_ROOT="/home/bsup/itron-automation"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="$PROJECT_ROOT/support_box/daily_env.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --pilot <teco|luma> --request-name <name> --meter-list-s3 <s3://...> [--date YYYYMMDD]

Example:
  $(basename "$0") --pilot teco --request-name may14_client_batch --meter-list-s3 s3://bucket/path/file.csv --date 20260514
EOF
}

PILOT=""
REQUEST_NAME=""
METER_LIST_S3=""
DATE_STR="$(date +%Y%m%d)"

while [ $# -gt 0 ]; do
  case "$1" in
    --pilot)
      PILOT="${2:-}"
      shift 2
      ;;
    --request-name)
      REQUEST_NAME="${2:-}"
      shift 2
      ;;
    --meter-list-s3)
      METER_LIST_S3="${2:-}"
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

if [ -z "$PILOT" ] || [ -z "$REQUEST_NAME" ] || [ -z "$METER_LIST_S3" ]; then
  echo "Missing required arguments."
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

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN is not set. Export it before running special-request PR automation."
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

LOG_DIR="$PROJECT_ROOT/logs"
RUN_LOG="$LOG_DIR/${PILOT}_special_${REQUEST_NAME}_${DATE_STR}.log"
mkdir -p "$LOG_DIR"

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

ssh -i "$ITRON_TUNNEL_KEY" \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=3 \
  -L "${ITRON_TUNNEL_PORT}:${ITRON_TUNNEL_TARGET}" \
  "$ITRON_TUNNEL_HOST" \
  -N &
TUNNEL_PID=$!

sleep 3

cd "$PROJECT_ROOT"
prepare_utils_repo
if OUTPUT=$("$PYTHON_BIN" -m app run-special-request \
  --pilot "$PILOT" \
  --request-name "$REQUEST_NAME" \
  --meter-list-s3 "$METER_LIST_S3" \
  --output-dir output/special 2>&1); then
  if REPO_OUTPUT=$("$PYTHON_BIN" -m app export-to-utils-repo \
    --pilot "$PILOT" \
    --date "$DATE_STR" \
    --scripts-dir "output/special/${REQUEST_NAME}/${PILOT}/scripts" \
    --create-pr 2>&1); then
    OUTPUT="${OUTPUT}

${REPO_OUTPUT}"
    printf "%s\n" "$OUTPUT" | tee "$RUN_LOG"
    MESSAGE=$(printf "%s" "$OUTPUT" | "$PYTHON_BIN" "$PROJECT_ROOT/support_box/build_gchat_message.py" \
      --pilot "$PILOT" \
      --date "$DATE_STR" \
      --status success \
      --mode special \
      --request-name "$REQUEST_NAME" \
      --meter-list-s3 "$METER_LIST_S3")
    notify_gchat "$MESSAGE"
  else
    STATUS=$?
    OUTPUT="${OUTPUT}

${REPO_OUTPUT}"
    printf "%s\n" "$OUTPUT" | tee "$RUN_LOG"
    MESSAGE=$(printf "%s" "$OUTPUT" | "$PYTHON_BIN" "$PROJECT_ROOT/support_box/build_gchat_message.py" \
      --pilot "$PILOT" \
      --date "$DATE_STR" \
      --status failure \
      --mode special \
      --request-name "$REQUEST_NAME" \
      --meter-list-s3 "$METER_LIST_S3" \
      --run-log "$RUN_LOG")
    notify_gchat "$MESSAGE"
    exit "$STATUS"
  fi
else
  STATUS=$?
  printf "%s\n" "$OUTPUT" | tee "$RUN_LOG"
  MESSAGE=$(printf "%s" "$OUTPUT" | "$PYTHON_BIN" "$PROJECT_ROOT/support_box/build_gchat_message.py" \
    --pilot "$PILOT" \
    --date "$DATE_STR" \
    --status failure \
    --mode special \
    --request-name "$REQUEST_NAME" \
    --meter-list-s3 "$METER_LIST_S3" \
    --run-log "$RUN_LOG")
  notify_gchat "$MESSAGE"
  exit "$STATUS"
fi
