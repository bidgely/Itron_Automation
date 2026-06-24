#!/bin/bash

# Support box runtime configuration for daily terminal runs.
# Load this file with: source /home/bsup/itron-automation/support_box/daily_env.sh

export AWS_ACCESS_KEY_ID="AKIASYKMFO4ORQN576XZ"
export AWS_SECRET_ACCESS_KEY="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"
export AWS_REGION="us-west-2"

export ITRON_RS_HOST="na-rs1.ctxwwf9dwnm1.us-east-1.redshift.amazonaws.com"
export ITRON_RS_PORT="5439"
export ITRON_RS_USER="sbatchu"
export ITRON_RS_DATABASE="bdw"
export ITRON_RS_PASSWORD="vBbzuhF85$"

export PILOT_10223_DB_HOST="127.0.0.1"
export PILOT_10223_DB_PORT="3308"
export PILOT_10223_DB_USER="dbread"
export PILOT_10223_DB_PASSWORD="B1dG3Ly"
export PILOT_10223_DB_DATABASE="bidgelydbprod"

export ITRON_UTILS_REPO_PATH="/home/bsup/Utils-automation"

export ITRON_PILOT_CONFIG_S3_URI="s3://bidgely-artifacts2/Murali/itron-automation/config/pilots.json"
export ITRON_PILOT_CONFIG_CACHE_PATH="/home/bsup/itron-automation/config_cache/pilots.latest.json"
export ITRON_PILOT_CONFIG_REFRESH_SECONDS="60"
export ITRON_CHAT_UPLOAD_S3_PREFIX="Murali_Users/special/chat_uploads"

export ITRON_TUNNEL_KEY="/home/bsup/.ssh/id_ed25519"
export ITRON_TUNNEL_HOST="sbatchu@jumphost-prodna2.bidgely.com"
export ITRON_TUNNEL_PORT="3308"
export ITRON_TUNNEL_TARGET="10.2.5.53:3306"

# Optional special-request prefixes.
# Convention:
#   <prefix>/<YYYYMMDD>/<request_name>.csv
# Example:
#   s3://bidgely-artifacts2/Murali_Users/special/teco/20260514/may14_client_batch.csv
export ITRON_SPECIAL_REQUEST_S3_PREFIX_TECO="s3://bidgely-artifacts2/Murali_Users/special/teco"
export ITRON_SPECIAL_REQUEST_S3_PREFIX_LUMA="s3://bidgely-artifacts2/Murali_Users/special/luma"

# UAT SSH tunnel (SMUD, PECAN)
export ITRON_UAT_TUNNEL_KEY="/home/bsup/.ssh/id_ed25519"
export ITRON_UAT_TUNNEL_HOST="sbatchu@jumphost-uat.bidgely.com"
export ITRON_UAT_TUNNEL_PORT="3311"
export ITRON_UAT_TUNNEL_TARGET="uat-rds.cmlamxremgnb.us-west-2.rds.amazonaws.com:3306"

# UAT MySQL (all UAT pilots share the same credentials)
export ITRON_UAT_DB_HOST="127.0.0.1"
export ITRON_UAT_DB_PORT="3311"
export ITRON_UAT_DB_USER="bprod"
export ITRON_UAT_DB_PASSWORD="uatRdSbPR0D6033"
export ITRON_UAT_DB_DATABASE="bidgelydbuat_itron"

# UAT Redshift
export WEATHER_REDSHIFT_HOST="uat-redshiftcluster-5nzk27mcdow7.cgxykwll3uce.us-west-2.redshift.amazonaws.com"
export WEATHER_REDSHIFT_PORT="5439"
export WEATHER_REDSHIFT_DATABASE="bdw"
export WEATHER_REDSHIFT_USER="sbatchu"
export WEATHER_REDSHIFT_PASSWORD='vBbzuhF85$'

# Optional local-only secret file for automation.
# Keep this on the support box only and do not commit it.
# Example: export GITHUB_TOKEN="replace_with_current_github_pat"
SECRETS_FILE="/home/bsup/itron-automation/support_box/daily_secrets.sh"
if [ -f "$SECRETS_FILE" ]; then
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
fi
