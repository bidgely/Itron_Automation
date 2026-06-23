#!/bin/bash

set -euo pipefail

PROJECT_ROOT="/home/bsup/itron-automation"
DATE_STR="$(date +%Y%m%d)"
ENV_FILE="$PROJECT_ROOT/support_box/daily_env.sh"
SPECIAL_PREFIX_DEFAULT="s3://bidgely-artifacts2/Murali_Users/special/luma"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE"
  exit 1
fi

source "$ENV_FILE"

SPECIAL_PREFIX="${ITRON_SPECIAL_REQUEST_S3_PREFIX_LUMA:-$SPECIAL_PREFIX_DEFAULT}"
SPECIAL_DATE_PREFIX="${SPECIAL_PREFIX%/}/${DATE_STR}/"

SPECIAL_FILE=""
if SPECIAL_LISTING=$(aws s3 ls "$SPECIAL_DATE_PREFIX" 2>/dev/null); then
  SPECIAL_FILE=$(printf "%s\n" "$SPECIAL_LISTING" | awk '/\.csv$/ {print $4; exit}')
fi

cd "$PROJECT_ROOT"
source .venv/bin/activate

if [ -n "$SPECIAL_FILE" ]; then
  REQUEST_NAME="${SPECIAL_FILE%.csv}"
  METER_LIST_S3="${SPECIAL_DATE_PREFIX}${SPECIAL_FILE}"
  echo "Special request detected for LUMA: ${METER_LIST_S3}"
  echo "Running LUMA special request first, then normal daily flow."
  ./support_box/run_special_request_and_create_pr.sh \
    --pilot luma \
    --request-name "$REQUEST_NAME" \
    --meter-list-s3 "$METER_LIST_S3" \
    --date "$DATE_STR"
  ./support_box/run_luma_daily.sh
else
  echo "No LUMA special request found for ${DATE_STR}. Running normal daily flow."
  ./support_box/run_luma_daily.sh
fi
