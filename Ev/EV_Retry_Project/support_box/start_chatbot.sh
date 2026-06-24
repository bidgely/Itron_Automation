#!/bin/bash
set -euo pipefail
source /home/bsup/itron-automation/support_box/daily_env.sh
export PYTHONUNBUFFERED=1
export GOOGLE_SERVICE_ACCOUNT_KEY="/home/bsup/itron-automation/service_account.json"
exec /home/bsup/itron-automation/.venv/bin/uvicorn chatbot.main:app --host 0.0.0.0 --port 8000
