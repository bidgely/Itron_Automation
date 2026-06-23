# Solar Monitor — Project README

## Overview

This project monitors solar estimation data across multiple utility pilots. It runs three automated jobs on the support box:

| Job | Script | Schedule |
|---|---|---|
| Daily solar analysis report | `main.py` | 11:00 AM IST (5:30 AM UTC) |
| Hourly solar + weather check | `weather_hourly_notifier.py` | Every hour at :05 IST (:35 UTC) |
| Daily DB snapshot | `db_snapshot.py` | 12:30 AM IST (7:00 PM UTC previous day) |

A **Google Chat chatbot** (`chatbot/`) runs as a `systemd` service and lets users trigger analysis, view missing users, and run hourly checks on demand.

---

## Project Structure

```
PythonProject/
├── pilots/
│   ├── pilots.json          ← The only file you edit to add/remove pilots
│   └── loader.py            ← Loads pilots.json from S3 with local backup + TTL cache
├── config.py                ← Loads all pilot config at startup
├── main.py                  ← Daily solar analysis
├── weather_hourly_notifier.py ← Hourly solar + weather check
├── db_snapshot.py           ← Daily DB snapshot of solar users
├── chatbot/                 ← FastAPI Google Chat bot
├── db/                      ← MySQL DB client
├── s3/                      ← S3 read helpers
├── processor/               ← Data processing logic
├── report/                  ← Report + chart generation
├── notifier/                ← Google Chat notification sender
└── utils/                   ← Logging helpers
```

---

## How to Add a New Pilot

> **No code changes needed. No box access needed. Just edit and upload one JSON file.**

### Step 1 — Edit `pilots/pilots.json`

Add a new entry to the `"pilots"` array:

```json
{
  "id": 10999,
  "name": "NEW PILOT UAT",
  "env": "uat",
  "db_name": "bidgelydbuat_itron",
  "s3_bucket": "bidgely-newpilot-itron-uat-external",
  "s3_prefix": "solar_usage_data/duration=15min/",
  "export_s3": "s3://bidgely-artifacts2/Murali_Users/NEWPILOT",
  "threshold": {"mode": "count", "value": 1}
}
```

#### Required fields

| Field | Description |
|---|---|
| `id` | Pilot ID (integer, must be **unique** — duplicates cause one pilot to be silently overwritten) |
| `name` | Display name shown in chatbot and reports |
| `env` | `"uat"` or `"prod"` — determines which shared DB credentials to use |
| `db_name` | MySQL database name for this pilot |
| `s3_bucket` | S3 bucket where solar estimation files land |
| `s3_prefix` | Prefix path inside the bucket (e.g. `solar_usage_data/duration=15min/`) |
| `export_s3` | S3 URI where reports/missing-UUID files are exported |
| `threshold` | When to trigger an hourly alert (see below) |

#### Threshold modes

```json
{"mode": "count",   "value": 1}      ← alert if missing >= 1 user
{"mode": "percent", "value": 0.20}   ← alert if missing >= 20% of expected users (ceil)
```

#### Optional fields

| Field | Description |
|---|---|
| `db_secret_arn` | AWS Secrets Manager ARN for DB credentials (PROD pilots that use Secrets Manager) |
| `variants` | List of sub-variants for a pilot (e.g. UAT + PRE). Each variant can override `s3_bucket`, `s3_prefix`, `export_s3`, and `name` |

#### Variant example (PECAN has two variants)

```json
"variants": [
  {"name": "PECAN UAT"},
  {
    "name": "PECAN PRE",
    "s3_bucket": "bidgely-pecan-itron-uat-external",
    "s3_prefix": "pre_solar_usage_data/duration=15min/",
    "export_s3": "s3://bidgely-artifacts2/Murali_Users/PECAN_PRE"
  }
]
```

### Step 2 — Upload to S3

```bash
aws s3 cp pilots/pilots.json s3://bidgely-artifacts2/Murali_Users/config/pilots.json
```

### Step 3 — Done

- The **cron jobs** pick up new pilots automatically on the next run (they re-read the JSON each time).
- The **chatbot** picks up new pilots within 60 seconds (TTL cache) — no restart needed.
- DB credentials for UAT pilots are shared (`UAT_DB_*` in `.env`). For PROD pilots, use `PROD_DB_*` or provide a `db_secret_arn`.

---

## Infrastructure Requirements

### Support Box

| Item | Path / Value |
|---|---|
| Project | `/home/ubuntu/PythonProject` |
| Python venv | `/home/ubuntu/PythonProject/.venv` |
| Env file | `/home/ubuntu/PythonProject/.env` |
| Daily cron script | `/home/ubuntu/solarEstimationCron.sh` |
| Hourly cron script | `/home/ubuntu/weatherHourlyCron.sh` |
| Chatbot service | `solar-chatbot` (systemd) |
| UAT SSH tunnel | `solar-tunnel-uat` (systemd, port `3316`) |
| PROD SSH tunnel | `solar-tunnel-prod` (systemd, port `3317`) |
| SSH key | `/home/ubuntu/.ssh/jumphost_uat_key` |

### `.env` File (required on box)

```env
GCHAT_ENABLED=true
GCHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/...

SNAPSHOT_BUCKET=bidgely-artifacts2
SNAPSHOT_PREFIX=Murali_Users/db_snapshots
MISSING_EXPORT_ENABLED=true

# Shared UAT DB credentials (used by all UAT pilots)
UAT_DB_HOST=127.0.0.1
UAT_DB_PORT=3316
UAT_DB_USER=bprod
UAT_DB_PASSWORD=<uat_password>

# Shared PROD DB credentials (used by all PROD pilots without a secret ARN)
PROD_DB_HOST=127.0.0.1
PROD_DB_PORT=3317
PROD_DB_USER=dbread
PROD_DB_PASSWORD=<prod_password>

# Per-pilot AWS profile override (only needed for prod read access)
PILOT_10223_AWS_PROFILE=tempna
```

> **Note:** Adding a new UAT pilot requires **zero changes** to `.env` — it inherits `UAT_DB_*` automatically.  
> For a new PROD pilot with its own DB secret, add `PILOT_<id>_AWS_PROFILE=tempna` if needed.

### AWS Requirements

- Box instance role needs read access to `bidgely-artifacts2` (config + artifact writes)
- `tempna` AWS profile (created by `assume_roles_prod.sh na 2`) needed for prod S3/RDS reads
- S3 buckets for each pilot must be accessible from the box (direct or via assumed role)

---

## Deploying Code Changes

### On your local machine

```bash
cd /Users/saimuralidhar/PycharmProjects
zip -r /tmp/PythonProject_deploy.zip PythonProject \
  --exclude "*.pyc" --exclude "__pycache__/*" \
  --exclude ".venv/*" --exclude "output/*" \
  --exclude "config_cache/*" --exclude "*.zip"
aws s3 cp /tmp/PythonProject_deploy.zip s3://bidgely-artifacts2/Murali_Users/PythonProject_deploy.zip
```

### On the support box

```bash
# Backup current
cp -r /home/ubuntu/PythonProject /home/ubuntu/PythonProject_backup_$(date +%Y%m%d_%H%M%S)

# Deploy
aws s3 cp s3://bidgely-artifacts2/Murali_Users/PythonProject_deploy.zip /tmp/
unzip -o /tmp/PythonProject_deploy.zip -d /home/ubuntu/

# Restore things not in the zip
cp /home/ubuntu/PythonProject_backup_*/. env /home/ubuntu/PythonProject/.env
cp -r /home/ubuntu/PythonProject_backup_*/.venv /home/ubuntu/PythonProject/.venv
mkdir -p /home/ubuntu/PythonProject/logs
mkdir -p /home/ubuntu/PythonProject/config_cache

# Restart chatbot
sudo systemctl restart solar-chatbot
sudo systemctl status solar-chatbot
```

> **Never forget to restore `.env` and `.venv` after deploying** — they are excluded from the zip.

### If `.venv` is missing or broken

```bash
cd /home/ubuntu/PythonProject
python3 -m venv --copies .venv
source .venv/bin/activate
pip install --upgrade pip
pip install boto3 pymysql pillow fastapi uvicorn
```

---

## Chatbot

The chatbot is a FastAPI app running as a systemd service, exposed via ngrok.

### Service commands

```bash
sudo systemctl status solar-chatbot
sudo systemctl restart solar-chatbot
sudo systemctl stop solar-chatbot
```

### View live logs

```bash
journalctl -u solar-chatbot -f
journalctl -u solar-chatbot -n 100
```

### Config file

```
/etc/systemd/system/solar-chatbot.service
```

The service uses `EnvironmentFile=/home/ubuntu/PythonProject/.env` — if `.env` is missing, the service will fail to start.

### Pilot config refresh (TTL)

The chatbot holds a 60-second in-memory TTL cache of `pilots.json`. When you upload a new `pilots.json` to S3:
- The chatbot automatically refreshes within 60 seconds.
- No restart needed for new pilots.

---

## Manual Runs

### Run DB snapshot for a specific date

```bash
cd /home/ubuntu/PythonProject
source .venv/bin/activate
set -a && source .env && set +a
python3 db_snapshot.py --date 2026-06-11
```

### Run hourly notifier for a specific hour

```bash
python3 weather_hourly_notifier.py --target_datetime 2026-06-11T10
```

### Run daily report for a specific date

```bash
python3 main.py --start_date 2026-06-11 --end_date 2026-06-11
```

All scripts auto-discover all pilots from `pilots.json` — no `--pilot_ids` flag needed unless you want to run for specific pilots only.

---

## Common Bugs & Fixes

### ❌ Chatbot fails to start — `EnvironmentFile not found`

**Symptom:** `journalctl -u solar-chatbot` shows `No such file or directory` for `.env`

**Fix:** Restore `.env` from backup:
```bash
cp /home/ubuntu/PythonProject_backup_*/.env /home/ubuntu/PythonProject/.env
sudo systemctl restart solar-chatbot
```

---

### ❌ Chatbot shows `Unable to load data for: endpoints`

**Symptom:** Running any chatbot action returns this error.

**Cause:** Chatbot was not restarted after a deployment.

**Fix:**
```bash
sudo systemctl restart solar-chatbot
```

---

### ❌ `No DB configuration available for pilot XXXXX`

**Symptom:** Hourly check or snapshot fails with this error.

**Causes & fixes:**

| Cause | Fix |
|---|---|
| Pilot added to `pilots.json` after the cron scripts started (module-level cache) | Re-run the script — cron scripts re-read JSON fresh each run |
| `UAT_DB_HOST` / `PROD_DB_HOST` missing from `.env` | Add shared DB credentials to `.env` |
| Wrong `env` value in `pilots.json` (e.g. typo `"UAT"` instead of `"uat"`) | Fix the `env` field in `pilots.json` and re-upload |

---

### ❌ `No DB snapshot found for <Pilot> on <date>`

**Symptom:** Chatbot analysis shows this warning.

**Cause:** `db_snapshot.py` did not run or failed for that date.

**Fix:** Run manually:
```bash
cd /home/ubuntu/PythonProject
source .venv/bin/activate
set -a && source .env && set +a
python3 db_snapshot.py --date <YYYY-MM-DD>
```

Check why the cron failed:
```bash
cat /home/ubuntu/PythonProject/logs/db_snapshot.log
```

---

### ❌ `Connection refused` on localhost MySQL port

**Symptom:** DB snapshot or analysis fails with connection refused.

**Cause:** SSH tunnel is down.

**Check:**
```bash
sudo systemctl status solar-tunnel-uat
sudo systemctl status solar-tunnel-prod
lsof -i :3316
lsof -i :3317
```

**Fix:**
```bash
sudo systemctl restart solar-tunnel-uat
sudo systemctl restart solar-tunnel-prod
```

---

### ❌ `ProfileNotFound: The config profile (tempna) could not be found`

**Cause:** The assumed-role session for PROD has expired.

**Fix:**
```bash
cd /home/ubuntu
./assume_roles_prod.sh na 2
```

---

### ❌ Two pilots have the same ID — one disappears from chatbot

**Symptom:** A pilot button is missing. The loader silently overwrites the first pilot with the second when IDs collide.

**Fix:** Check and fix `pilots.json` on S3:
```bash
aws s3 cp s3://bidgely-artifacts2/Murali_Users/config/pilots.json - | python3 -m json.tool
```

Ensure every pilot has a **unique `id`** field.

---

### ❌ `config_cache/` directory missing — S3 load fails on startup

**Symptom:** First run after a fresh deployment fails with `No local backup found`.

**Fix:** Seed the local cache from S3:
```bash
mkdir -p /home/ubuntu/PythonProject/config_cache
aws s3 cp s3://bidgely-artifacts2/Murali_Users/config/pilots.json \
  /home/ubuntu/PythonProject/config_cache/pilots.latest.json
```

---

### ❌ `logs/` directory missing — cron output silently lost

**Fix:**
```bash
mkdir -p /home/ubuntu/PythonProject/logs
```

---

### ❌ `GCHAT_ENABLED=false` — no notifications sent

**Symptom:** Scripts run but nothing appears in Google Chat.

**Fix:** Ensure `.env` has:
```env
GCHAT_ENABLED=true
GCHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/...
```

---

## Useful Diagnostic Commands

```bash
# Check cron is running
crontab -l
systemctl status cron --no-pager

# Check tunnel services
sudo systemctl status solar-tunnel-uat
sudo systemctl status solar-tunnel-prod

# Check DB connectivity
mysql -h 127.0.0.1 -P 3316 -ubprod -p -e "select 1;"
mysql -h 127.0.0.1 -P 3317 -udbread -p -e "select 1;"

# Check AWS identity
aws sts get-caller-identity
aws sts get-caller-identity --profile tempna

# Check current pilots.json on S3
aws s3 cp s3://bidgely-artifacts2/Murali_Users/config/pilots.json - | python3 -m json.tool

# Check what's in local backup cache
cat /home/ubuntu/PythonProject/config_cache/pilots.latest.json | python3 -m json.tool

# View cron logs
tail -f /home/ubuntu/solarEstimationCron.log
tail -f /home/ubuntu/weather_hourly_notifier.log
cat /home/ubuntu/PythonProject/logs/db_snapshot.log
```
