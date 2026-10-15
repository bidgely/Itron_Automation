#!/bin/bash

# Support box runtime configuration for daily terminal runs.
# Load this file with: source /home/bsup/itron-automation/support_box/daily_env.sh

export AWS_REGION="us-west-2"

export ITRON_RS_HOST="bidgely-prod-na-cluster.trino.galaxy.starburst.io"
export ITRON_RS_PORT="443"
export ITRON_RS_USER="read-only-user@bidgely.galaxy.starburst.io"
export ITRON_RS_DATABASE="delta_prodna"
export ITRON_RS_PASSWORD='GXY$n1rqUNHGeWt9M2yLmpQiwbS1mbkglMihyAlmNcKRX6D2jSspuMSxyMOW'

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
export ITRON_TUNNEL_TARGET="prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306"

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
export WEATHER_REDSHIFT_HOST="bidgely-uat-cluster.trino.galaxy.starburst.io"
export WEATHER_REDSHIFT_PORT="443"
export WEATHER_REDSHIFT_DATABASE="delta_uat"
export WEATHER_REDSHIFT_USER="read-only-user@bidgely.galaxy.starburst.io"
export WEATHER_REDSHIFT_PASSWORD='GXY$n1rqUNHGeWt9M2yLmpQiwbS1mbkglMihyAlmNcKRX6D2jSspuMSxyMOW'

# Optional local-only secret file for automation.
# Keep this on the support box only and do not commit it.
# Example: export GITHUB_TOKEN="replace_with_current_github_pat"
SECRETS_FILE="/home/bsup/itron-automation/support_box/daily_secrets.sh"
if [ -f "$SECRETS_FILE" ]; then
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
fi
