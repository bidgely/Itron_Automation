#!/bin/bash

set -euo pipefail

PROJECT_ROOT="/home/bsup/itron-automation"
DATE_STR="$(date +%Y%m%d)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="$PROJECT_ROOT/support_box/daily_env.sh"

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

# Dynamically fetch all configured pilot keys from the JSON config
PILOT_KEYS=$("$PYTHON_BIN" - <<'PY'
from app.pilots import get_supported_pilot_keys
print("\n".join(get_supported_pilot_keys()))
PY
)

if [ -z "$PILOT_KEYS" ]; then
  echo "No pilots found in config. Exiting."
  exit 1
fi

echo "Pilots to run: $(echo "$PILOT_KEYS" | tr '\n' ' ')"

FAILED_PILOTS=""

for PILOT in $PILOT_KEYS; do
  echo ""
  echo "========================================="
  echo "Starting auto flow for pilot: $PILOT"
  echo "========================================="
  if ./support_box/run_pilot_auto.sh --pilot "$PILOT" --date "$DATE_STR"; then
    echo "[$PILOT] completed successfully."
  else
    echo "[$PILOT] FAILED."
    FAILED_PILOTS="$FAILED_PILOTS $PILOT"
  fi
done

if [ -n "$FAILED_PILOTS" ]; then
  echo ""
  echo "The following pilots failed:$FAILED_PILOTS"
  exit 1
fi

echo ""
echo "All pilots completed successfully."
