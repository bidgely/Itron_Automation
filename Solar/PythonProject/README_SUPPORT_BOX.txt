# Support Box Setup

This document describes how to run the project on the support box.

## Paths

- Project: `/home/ubuntu/PythonProject`
- Daily script: `/home/ubuntu/solarEstimationCron.sh`
- Hourly script: `/home/ubuntu/weatherHourlyCron.sh`
- UAT jump key: `/home/ubuntu/.ssh/jumphost_uat_key`

## What Runs on the Box

There are 2 scheduled jobs:

1. Hourly Solar + Weather report
2. Daily OG full-day report

## Time Zone

The support box runs in `UTC`.

Current cron intent:

- Hourly report at `:05 IST`
- Daily report at `11:00 AM IST`

Equivalent UTC cron times:

- Hourly: `35 * * * *`
- Daily: `30 5 * * *`

## Cron Entries

Add these with `crontab -e`:

```cron
35 * * * * /home/ubuntu/weatherHourlyCron.sh >> /home/ubuntu/weather_hourly_notifier.log 2>&1
30 5 * * * /home/ubuntu/solarEstimationCron.sh >> /home/ubuntu/solarEstimationCron.log 2>&1
```

Verify with:

```bash
crontab -l
```

## Python Environment

The zipped project does not include `.venv`, so recreate it after deploying a fresh zip:

```bash
cd /home/ubuntu/PythonProject
python3 -m venv --copies /home/ubuntu/PythonProject/.venv
source /home/ubuntu/PythonProject/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install boto3 pymysql pillow
```

If the shell is stuck on an old broken venv, reset it first:

```bash
deactivate || true
hash -r
unset VIRTUAL_ENV
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

## Deploying a Fresh Zip

On local machine:

```bash
cd /Users/saimuralidhar/PycharmProjects
zip -r ~/Downloads/PythonProject_supportbox.zip PythonProject \
  -x "PythonProject/output/*" "PythonProject/**/__pycache__/*" "PythonProject/.venv/*"
aws s3 cp ~/Downloads/PythonProject_supportbox.zip s3://bidgely-artifacts2/Murali_Users/PythonProject_supportbox.zip
```

On the support box:

```bash
aws s3 cp s3://bidgely-artifacts2/Murali_Users/PythonProject_supportbox.zip /home/ubuntu/PythonProject_supportbox.zip
mv /home/ubuntu/PythonProject /home/ubuntu/PythonProject_backup_$(date +%Y%m%d_%H%M%S)
cd /home/ubuntu
unzip -o PythonProject_supportbox.zip
```

## Hourly Report

The hourly report sends:

- Solar Estimate counts for the previous hour
- Weather Data counts for the previous hour
- Missing solar UUID files uploaded to S3 for pilots that have missing users

It uses per-pilot thresholds:

- `10014` SMUD UAT: notify if missing users `>= 1`
- `10118` PECAN UAT: notify if missing users are `>= 20%`, rounded up with `ceil`
- `10223` LUMA PROD: notify if missing users `>= 1`

Example for PECAN:

- expected users = `9`
- `20% of 9 = 1.8`
- `ceil(1.8) = 2`
- notify only when missing users are `>= 2`

Weather missing zipcode data also triggers a notification.

### Hourly Script Responsibilities

`/home/ubuntu/weatherHourlyCron.sh` should:

1. refresh prod assumed role
2. kill stale tunnels
3. open fresh UAT/prod tunnels
4. activate the project venv
5. export Google Chat settings
6. export DB settings
7. export weather bucket settings
8. upload missing UUID files to S3 for pilots with missing users
9. run `weather_hourly_notifier.py`
10. clean up tunnel PIDs on exit

### Recommended Hourly Ports

- UAT DB tunnel: `3310`
- PROD DB tunnel: `3311` or another dedicated local port

### Example Hourly Script

```bash
#!/bin/bash
set -e

cleanup() {
  [ -n "$UAT_TUNNEL_PID" ] && kill "$UAT_TUNNEL_PID" 2>/dev/null || true
  [ -n "$PROD_TUNNEL_PID" ] && kill "$PROD_TUNNEL_PID" 2>/dev/null || true
}

trap cleanup EXIT

cd /home/ubuntu
./assume_roles_prod.sh na 2

pkill -f "3310:uat-rds.cmlamxremgnb.us-west-2.rds.amazonaws.com:3306" || true
pkill -f "3311:prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306" || true

ssh -i /home/ubuntu/.ssh/jumphost_uat_key -N -L 3310:uat-rds.cmlamxremgnb.us-west-2.rds.amazonaws.com:3306 sbatchu@jumphost-uat.bidgely.com &
UAT_TUNNEL_PID=$!

ssh -i /home/ubuntu/.ssh/jumphost_uat_key -N -L 3311:prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306 sbatchu@jumphost-prodna2.bidgely.com &
PROD_TUNNEL_PID=$!

sleep 10

cd /home/ubuntu/PythonProject
source /home/ubuntu/PythonProject/.venv/bin/activate

export GCHAT_ENABLED=true
export GCHAT_WEBHOOK_URL="YOUR_WEBHOOK"

export PILOT_10014_DB_HOST="127.0.0.1"
export PILOT_10014_DB_PORT="3310"
export PILOT_10014_DB_USER="bprod"
export PILOT_10014_DB_PASSWORD="uatRdSbPR0D6033"
export PILOT_10014_DB_DATABASE="bidgelydbuat_itron"

export PILOT_10118_DB_HOST="127.0.0.1"
export PILOT_10118_DB_PORT="3310"
export PILOT_10118_DB_USER="bprod"
export PILOT_10118_DB_PASSWORD="uatRdSbPR0D6033"
export PILOT_10118_DB_DATABASE="bidgelydbuat_itron"

export PILOT_10223_DB_HOST="127.0.0.1"
export PILOT_10223_DB_PORT="3311"
export PILOT_10223_DB_USER="dbread"
export PILOT_10223_DB_PASSWORD="B1dG3Ly"
export PILOT_10223_DB_DATABASE="bidgelydbprod"

export PILOT_10223_AWS_PROFILE="tempna"
unset PILOT_10223_EXPORT_AWS_PROFILE

export PILOT_10014_WEATHER_DATA_BUCKET="bidgely-data-warehouse-uat"
export PILOT_10014_WEATHER_DATA_PREFIX="weather-data/weather-data-raw/v3/weather_data_type=FORECAST/duration=1h/country=US"

export PILOT_10118_WEATHER_DATA_BUCKET="bidgely-data-warehouse-uat"
export PILOT_10118_WEATHER_DATA_PREFIX="weather-data/weather-data-raw/v3/weather_data_type=FORECAST/duration=1h/country=US"

export PILOT_10223_WEATHER_DATA_BUCKET="bidgely-data-warehouse-prod-na"
export PILOT_10223_WEATHER_DATA_PREFIX="weather-data/weather-data-raw/v3/weather_data_type=FORECAST/duration=1h/country=US"

python3 weather_hourly_notifier.py --pilot_ids 10014 10118 10223
```

## Daily OG Report

The daily OG script sends the previous day's consolidated report.

### Daily Script Responsibilities

`/home/ubuntu/solarEstimationCron.sh` should:

1. refresh prod assumed role
2. open UAT and PROD tunnels
3. activate the project venv
4. export Google Chat settings
5. export DB settings
6. export Redshift settings for UAT weather checks
7. export prod weather API settings if using `--weather_check`
8. run `main.py` for yesterday

### Daily Script Notes

- For prod pilot `10223`:
  - read should use `tempna`
  - write should use the box's default AWS creds
- This is achieved with:

```bash
export PILOT_10223_AWS_PROFILE="tempna"
unset PILOT_10223_EXPORT_AWS_PROFILE
```

### Example Daily Script

```bash
#!/bin/bash
set -e

cd /home/ubuntu
./assume_roles_prod.sh na 2

ssh -i /home/ubuntu/.ssh/jumphost_uat_key -f -N -L 3307:uat-rds.cmlamxremgnb.us-west-2.rds.amazonaws.com:3306 sbatchu@jumphost-uat.bidgely.com
ssh -i /home/ubuntu/.ssh/jumphost_uat_key -f -N -L 3308:prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306 sbatchu@jumphost-prodna2.bidgely.com

sleep 5

cd /home/ubuntu/PythonProject
source /home/ubuntu/PythonProject/.venv/bin/activate

export GCHAT_ENABLED=true
export GCHAT_WEBHOOK_URL="YOUR_WEBHOOK"

export PILOT_10014_DB_HOST="127.0.0.1"
export PILOT_10014_DB_PORT="3307"
export PILOT_10014_DB_USER="bprod"
export PILOT_10014_DB_PASSWORD="uatRdSbPR0D6033"
export PILOT_10014_DB_DATABASE="bidgelydbuat_itron"

export PILOT_10118_DB_HOST="127.0.0.1"
export PILOT_10118_DB_PORT="3307"
export PILOT_10118_DB_USER="bprod"
export PILOT_10118_DB_PASSWORD="uatRdSbPR0D6033"
export PILOT_10118_DB_DATABASE="bidgelydbuat_itron"

export PILOT_10223_DB_HOST="127.0.0.1"
export PILOT_10223_DB_PORT="3308"
export PILOT_10223_DB_USER="dbread"
export PILOT_10223_DB_PASSWORD="B1dG3Ly"
export PILOT_10223_DB_DATABASE="bidgelydbprod"

export WEATHER_REDSHIFT_HOST="uat-redshiftcluster-5nzk27mcdow7.cgxykwll3uce.us-west-2.redshift.amazonaws.com"
export WEATHER_REDSHIFT_PORT="5439"
export WEATHER_REDSHIFT_DATABASE="bdw"
export WEATHER_REDSHIFT_USER="sbatchu"
export WEATHER_REDSHIFT_PASSWORD='vBbzuhF85$'

export PILOT_10223_AWS_PROFILE="tempna"
unset PILOT_10223_EXPORT_AWS_PROFILE

python3 main.py --pilot_ids 10014 10118 10223 --start_date "$(date -d 'yesterday' +%F)" --end_date "$(date -d 'yesterday' +%F)"
```

If prod weather classification is required in daily script, also add:

```bash
export PILOT_10223_WEATHER_LOOKUP_MODE="api"
export PILOT_10223_USER_API_BASE_URL="https://naapi2-external.bidgely.com/v2.0/users/"
export PILOT_10223_USER_API_TOKEN_ENV="PILOT_10223_USER_API_BEARER_TOKEN"
export PILOT_10223_USER_API_BEARER_TOKEN="YOUR_PROD_TOKEN"
export PILOT_10223_WEATHER_DATA_BUCKET="bidgely-data-warehouse-prod-na"
export PILOT_10223_WEATHER_DATA_PREFIX="weather-data/weather-data-raw/v3/weather_data_type=FORECAST/duration=1h/country=US"
```

## AWS Identity Model

### Box Default Credentials

The support box naturally uses its instance role when no profile is set.

Check with:

```bash
aws sts get-caller-identity
```

### Prod Read Credentials

For prod reads, use `tempna` created by:

```bash
cd /home/ubuntu
./assume_roles_prod.sh na 2
aws sts get-caller-identity --profile tempna
```

### Read vs Write for Prod

Current intended behavior:

- Read prod source data with `tempna`
- Write artifacts with the box default AWS creds

That is why the scripts should use:

```bash
export PILOT_10223_AWS_PROFILE="tempna"
unset PILOT_10223_EXPORT_AWS_PROFILE
```

## Manual Verification Commands

### Check Cron

```bash
crontab -l
systemctl status cron --no-pager
grep CRON /var/log/syslog | tail -n 50
```

### Check Logs

```bash
tail -n 200 /home/ubuntu/solarEstimationCron.log
tail -n 200 /home/ubuntu/weather_hourly_notifier.log
```

### Check DB Tunnels

```bash
lsof -i :3307
lsof -i :3308
lsof -i :3310
lsof -i :3311
```

### Check DB Connectivity

UAT:

```bash
mysql -h 127.0.0.1 -P 3307 -ubprod -puatRdSbPR0D6033 -e "select 1;"
```

PROD:

```bash
mysql -h 127.0.0.1 -P 3308 -udbread -pB1dG3Ly -e "select 1;"
```

### Check AWS Identity

```bash
aws sts get-caller-identity
aws sts get-caller-identity --profile tempna
env | grep ^AWS
```

### Check S3 Access

Default box creds:

```bash
aws s3 ls s3://bidgely-artifacts2/Murali_Users/LUMA/
```

Prod read profile:

```bash
aws s3 ls s3://bidgely-luma-prod-external --profile tempna
```

### Check UAT Redshift Access

```bash
timeout 10 nc -vz uat-redshiftcluster-5nzk27mcdow7.cgxykwll3uce.us-west-2.redshift.amazonaws.com 5439
PGPASSWORD='vBbzuhF85$' psql -h uat-redshiftcluster-5nzk27mcdow7.cgxykwll3uce.us-west-2.redshift.amazonaws.com -p 5439 -U sbatchu -d bdw
```

### Check PROD Redshift Access

```bash
timeout 10 nc -vz na-rs1.ctxwwf9dwnm1.us-east-1.redshift.amazonaws.com 5439
PGPASSWORD='vBbzuhF85$' psql -h na-rs1.ctxwwf9dwnm1.us-east-1.redshift.amazonaws.com -p 5439 -U sbatchu -d bdw
```

## Common Problems

### `ProfileNotFound: tempna`

Run:

```bash
cd /home/ubuntu
./assume_roles_prod.sh na 2
```

### `Connection refused` on localhost MySQL port

The SSH tunnel is not running or is stale.

Check:

```bash
lsof -i :3307
lsof -i :3308
```

Kill stale tunnel:

```bash
kill -9 <pid>
```

### `Lost connection to MySQL server during query`

Usually a stale tunnel. Restart tunnel cleanly.

### `Google Chat disabled (GCHAT_ENABLED=false)`

Make sure script exports:

```bash
export GCHAT_ENABLED=true
export GCHAT_WEBHOOK_URL="YOUR_WEBHOOK"
```

### `Reason classification skipped (USER_API_BEARER_TOKEN not configured)`

This means prod weather API mode is active but the prod token env was not exported.

### `Weather analysis skipped (Redshift lookup failed: psql is not installed on this machine)`

Install:

```bash
sudo apt update
sudo apt install postgresql-client -y
```

## Quick Manual Runs

### Hourly Script

```bash
/home/ubuntu/weatherHourlyCron.sh
```

### Daily Script

```bash
/home/ubuntu/solarEstimationCron.sh
```

### Direct Hourly Notifier Test

```bash
cd /home/ubuntu/PythonProject
source /home/ubuntu/PythonProject/.venv/bin/activate
python3 weather_hourly_notifier.py --pilot_ids 10014 10118 10223 --target_datetime 2026-04-21T05
```

### Direct Prod Daily Test

```bash
cd /home/ubuntu/PythonProject
source /home/ubuntu/PythonProject/.venv/bin/activate
export PILOT_10223_AWS_PROFILE="tempna"
unset PILOT_10223_EXPORT_AWS_PROFILE
python3 main.py --pilot_ids 10223 --start_date 2026-04-19 --end_date 2026-04-19
```
