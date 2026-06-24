#!/bin/bash

set -euo pipefail

PROJECT_ROOT="/home/bsup/itron-automation"
DATE_STR="$(date +%Y%m%d)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="$PROJECT_ROOT/support_box/daily_env.sh"

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
cd "$PROJECT_ROOT"
source .venv/bin/activate

SPECIAL_PREFIX=$("$PYTHON_BIN" - "$PILOT" <<'PY'
import os
import sys

from app.pilots import get_pilot_definition

pilot = get_pilot_definition(sys.argv[1])
if pilot.special_request_s3_prefix:
    print(pilot.special_request_s3_prefix)
else:
    bucket = os.environ.get("ITRON_S3_BUCKET", "bidgely-artifacts2")
    print(f"s3://{bucket}/Murali_Users/special/{pilot.key}")
PY
)

SPECIAL_DATE_PREFIX="${SPECIAL_PREFIX%/}/${DATE_STR}/"

SPECIAL_FILE=""
if SPECIAL_LISTING=$(aws s3 ls "$SPECIAL_DATE_PREFIX" 2>/dev/null); then
  SPECIAL_FILE=$(printf "%s\n" "$SPECIAL_LISTING" | awk '/\.csv$/ {print $4; exit}')
fi

if [ -n "$SPECIAL_FILE" ]; then
  REQUEST_NAME="${SPECIAL_FILE%.csv}"
  METER_LIST_S3="${SPECIAL_DATE_PREFIX}${SPECIAL_FILE}"
  echo "Special request detected for ${PILOT}: ${METER_LIST_S3}"
  echo "Running ${PILOT} special request first, then normal daily flow."
  ./support_box/run_special_request_and_create_pr.sh \
    --pilot "$PILOT" \
    --request-name "$REQUEST_NAME" \
    --meter-list-s3 "$METER_LIST_S3" \
    --date "$DATE_STR"
  ./support_box/run_pilot_daily.sh --pilot "$PILOT" --date "$DATE_STR"
else
  echo "No ${PILOT} special request found for ${DATE_STR}. Running normal daily flow."
  ./support_box/run_pilot_daily.sh --pilot "$PILOT" --date "$DATE_STR"
fi
