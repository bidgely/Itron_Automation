# Itron Automation

Automated meter recovery pipeline for energy utility pilots (TECO, LUMA, SMUD, PECAN, and any future pilots).

---

## What It Does

For each configured pilot, the system:

1. **Connects to MySQL** via SSH tunnel and extracts meter lists
2. **Loads data into Redshift** staging tables and runs bucket analysis
3. **Generates MySQL update scripts** (mark completed, retry, etc.)
4. **Creates a PR** in the Utils repo with the generated scripts
5. **Sends a summary** to Google Chat

---

## How to Add a New Pilot

> **Only one step required — no code changes, no support box access needed.**

Edit `pilots.json` on S3:
```
s3://bidgely-artifacts2/Murali/itron-automation/config/pilots.json
```

Add a new entry:
```json
{
  "key": "newpilot",
  "display_name": "NEW PILOT",
  "pilot_id": 99999,
  "environment": "prod",
  "checkforev_zero_min_id": 995,
  "redshift_full_table": null,
  "redshift_request_sent_table": null,
  "redshift_checkforev_zero_table": null,
  "special_request_s3_prefix": "s3://bidgely-artifacts2/Murali_Users/special/newpilot"
}
```

**Key fields:**

| Field | Description |
|---|---|
| `key` | Short lowercase name (used in file paths, logs, GChat) |
| `display_name` | Display name shown in GChat and PRs |
| `pilot_id` | MySQL pilot ID |
| `environment` | `"prod"` or `"uat"` — auto-selects the right DB and tunnel |
| `checkforev_zero_min_id` | Usually `995`, change only if needed |
| `special_request_s3_prefix` | S3 path where special request CSVs are placed |

**Rules:**
- `environment: "prod"` → uses prod SSH tunnel (port `3308`) + prod MySQL credentials
- `environment: "uat"` → uses UAT SSH tunnel (port `3311`) + UAT MySQL credentials
- The cron picks up new pilots automatically within 60 seconds — no restart needed

---

## Architecture

```
S3 pilots.json
     |
     v
Cron (every 4 hours)
     |
     v
run_all_pilots_auto.sh
     |
     ├── For each pilot in pilots.json
     |       |
     |       v
     |   run_pilot_auto.sh
     |       |
     |       ├── Check for special request CSV in S3
     |       |       |
     |       |       └── If found: run_special_request_and_create_pr.sh
     |       |
     |       └── run_pilot_daily.sh
     |               |
     |               ├── Open SSH tunnel (prod or UAT based on pilot)
     |               ├── python -m app run-pilot-flow-and-create-pr
     |               └── Send GChat notification
     |
     v
Google Chat notification + GitHub PR
```

---

## Environments

### Prod (TECO, LUMA)
| Setting | Value |
|---|---|
| SSH Tunnel Port | `3308` |
| Jumphost | `sbatchu@jumphost-prodna2.bidgely.com` |
| RDS Target | `prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306` |
| DB User | `dbread` |
| DB Name | `bidgelydbprod` |

### UAT (SMUD, PECAN)
| Setting | Value |
|---|---|
| SSH Tunnel Port | `3311` |
| Jumphost | `sbatchu@jumphost-prodna2.bidgely.com` |
| RDS Target | `uat-rds.cmlamxremgnb.us-west-2.rds.amazonaws.com:3306` |
| DB User | `bprod` |
| DB Name | `bidgelydbuat_itron` |

---

## Required Environment Variables

Set in `/home/bsup/itron-automation/support_box/daily_env.sh` (committed)
and `/home/bsup/itron-automation/support_box/daily_secrets.sh` (support box only, never commit).

### AWS
```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-west-2"
```

### Redshift (Prod)
```bash
export ITRON_RS_HOST="..."
export ITRON_RS_PORT="5439"
export ITRON_RS_USER="..."
export ITRON_RS_PASSWORD="..."
export ITRON_RS_DATABASE="bdw"
```

### Redshift (UAT)
```bash
export WEATHER_REDSHIFT_HOST="..."
export WEATHER_REDSHIFT_PORT="5439"
export WEATHER_REDSHIFT_USER="..."
export WEATHER_REDSHIFT_PASSWORD="..."
export WEATHER_REDSHIFT_DATABASE="bdw"
```

### Prod MySQL Tunnel
```bash
export ITRON_TUNNEL_KEY="/home/bsup/.ssh/id_ed25519"
export ITRON_TUNNEL_HOST="sbatchu@jumphost-prodna2.bidgely.com"
export ITRON_TUNNEL_PORT="3308"
export ITRON_TUNNEL_TARGET="prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306"
export PILOT_10223_DB_HOST="127.0.0.1"
export PILOT_10223_DB_PORT="3308"
export PILOT_10223_DB_USER="dbread"
export PILOT_10223_DB_PASSWORD="..."
export PILOT_10223_DB_DATABASE="bidgelydbprod"
```

### UAT MySQL Tunnel
```bash
export ITRON_UAT_TUNNEL_KEY="/home/bsup/.ssh/id_ed25519"
export ITRON_UAT_TUNNEL_HOST="sbatchu@jumphost-prodna2.bidgely.com"
export ITRON_UAT_TUNNEL_PORT="3311"
export ITRON_UAT_TUNNEL_TARGET="uat-rds.cmlamxremgnb.us-west-2.rds.amazonaws.com:3306"
export ITRON_UAT_DB_HOST="127.0.0.1"
export ITRON_UAT_DB_PORT="3311"
export ITRON_UAT_DB_USER="bprod"
export ITRON_UAT_DB_PASSWORD="..."
export ITRON_UAT_DB_DATABASE="bidgelydbuat_itron"
```

### Secrets (daily_secrets.sh only — never commit)
```bash
export GITHUB_TOKEN="ghp_..."         # GitHub PAT with repo scope
export GCHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/..."
```

---

## Cron Schedule

```
0 */4 * * * /bin/bash /home/bsup/itron-automation/support_box/run_all_pilots_auto.sh >> /home/bsup/itron-automation/logs/all_pilots_auto.log 2>&1
```

Runs every 4 hours: `00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC`

---

## Deployment

### From local machine:
```bash
cd "/Users/saimuralidhar/Documents"
tar --exclude="New project/.venv" -czf itron-automation-dynamic-pilots.tar.gz "New project"
aws s3 cp itron-automation-dynamic-pilots.tar.gz s3://bidgely-artifacts2/Murali_Users/
```

### On support box:
```bash
cd /home/bsup
aws s3 cp s3://bidgely-artifacts2/Murali_Users/itron-automation-dynamic-pilots.tar.gz .
rm -rf /home/bsup/itron-automation-new && mkdir -p /home/bsup/itron-automation-new
tar -xzf itron-automation-dynamic-pilots.tar.gz -C /home/bsup/itron-automation-new --strip-components=1
sudo systemctl stop itron-chatbot
mv /home/bsup/itron-automation /home/bsup/itron-automation-backup-$(date +%Y%m%d%H%M%S)
mv /home/bsup/itron-automation-new /home/bsup/itron-automation
cd /home/bsup/itron-automation && rm -rf .venv
python3.10 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
chmod +x /home/bsup/itron-automation/support_box/*.sh
# Restore secrets (recreate daily_secrets.sh — it is NOT in the tarball)
cat > /home/bsup/itron-automation/support_box/daily_secrets.sh << 'EOF'
#!/bin/bash
export GITHUB_TOKEN="ghp_..."
export GCHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/..."
EOF
chmod 600 /home/bsup/itron-automation/support_box/daily_secrets.sh
sudo systemctl restart itron-chatbot && sudo systemctl status itron-chatbot
```

> **Important:** `daily_secrets.sh` is wiped on every deploy since it is not in the repo.
> Always recreate it after deploying.

---

## Common Issues & Fixes

### 1. Chatbot not starting (`status=127`)
**Cause:** `.venv` was created in a staging directory and then moved — shebang paths are broken.

**Fix:**
```bash
cd /home/bsup/itron-automation
rm -rf .venv
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart itron-chatbot
```

---

### 2. MySQL `Lost connection` / `Connection timed out`
**Cause:** SSH tunnel target is wrong or prod/UAT RDS endpoints are swapped in `daily_env.sh`.

**Fix:** Verify tunnel targets:
```bash
grep TUNNEL_TARGET /home/bsup/itron-automation/support_box/daily_env.sh
```

Should show:
```
export ITRON_TUNNEL_TARGET="prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306"
export ITRON_UAT_TUNNEL_TARGET="uat-rds.cmlamxremgnb.us-west-2.rds.amazonaws.com:3306"
```

Test tunnel manually:
```bash
pkill -f "ssh.*3308" || true
ssh -i "/home/bsup/.ssh/id_ed25519" \
  -L "3308:prodna2-rds.clecyrirlzdq.us-east-1.rds.amazonaws.com:3306" \
  "sbatchu@jumphost-prodna2.bidgely.com" -N &
sleep 10
mysql -h 127.0.0.1 -P 3308 -u dbread -p bidgelydbprod -e "SELECT 1;"
```

---

### 3. `GITHUB_TOKEN is not set`
**Cause:** `daily_secrets.sh` was wiped during deployment or never created.

**Fix:**
```bash
cat > /home/bsup/itron-automation/support_box/daily_secrets.sh << 'EOF'
#!/bin/bash
export GITHUB_TOKEN="ghp_..."
export GCHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/..."
EOF
chmod 600 /home/bsup/itron-automation/support_box/daily_secrets.sh
```

---

### 4. GitHub PR creation failed: `Bad credentials 401`
**Cause:** GitHub PAT has expired.

**Fix:** Generate a new PAT at `https://github.com/settings/tokens/new` with `repo` scope, then:
```bash
sed -i 's|export GITHUB_TOKEN=.*|export GITHUB_TOKEN="ghp_newtoken"|' \
  /home/bsup/itron-automation/support_box/daily_secrets.sh
sudo systemctl restart itron-chatbot
```

---

### 5. GChat notifications not sending
**Cause:** `GCHAT_WEBHOOK_URL` not set in `daily_secrets.sh`.

**Fix:**
```bash
grep GCHAT_WEBHOOK_URL /home/bsup/itron-automation/support_box/daily_secrets.sh
```

If missing, add it:
```bash
echo 'export GCHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/..."' \
  >> /home/bsup/itron-automation/support_box/daily_secrets.sh
```

Test:
```bash
source /home/bsup/itron-automation/support_box/daily_env.sh
curl -X POST "$GCHAT_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{"text": "test message"}'
```

---

### 6. Cron not running / no log files
**Cause:** Cron hasn't fired yet, or `logs/` directory doesn't exist.

**Fix:**
```bash
# Check cron schedule
crontab -l

# Create logs directory
mkdir -p /home/bsup/itron-automation/logs

# Test manually
/bin/bash /home/bsup/itron-automation/support_box/run_all_pilots_auto.sh \
  >> /home/bsup/itron-automation/logs/all_pilots_auto.log 2>&1 &
tail -f /home/bsup/itron-automation/logs/all_pilots_auto.log
```

---

### 7. Pilot returns 0 meters (UAT pilots — SMUD, PECAN)
**Cause:** UAT DB credentials or tunnel not configured correctly.

**Fix:** Verify UAT vars are set:
```bash
source /home/bsup/itron-automation/support_box/daily_env.sh
echo "UAT_DB=$ITRON_UAT_DB_HOST:$ITRON_UAT_DB_PORT"
echo "UAT_TUNNEL=$ITRON_UAT_TUNNEL_TARGET"
```

---

### 8. `bind: Address already in use` on port 3308/3311
**Cause:** A previous SSH tunnel process is still running.

**Fix:**
```bash
pkill -9 -f "ssh.*-L" || true
sleep 2
ss -tlnp | grep -E "3308|3311"
```

---

### 9. Cron runs twice / duplicate pilot output in logs
**Cause:** Two instances of `run_all_pilots_auto.sh` running simultaneously.

**Fix:**
```bash
pkill -f "run_all_pilots\|run_pilot\|run_luma\|run_teco" || true
pkill -f "ssh.*330" || true
```

---

## 30-Day Result Retention

Run summaries are saved to `chatbot_state/runs/` as JSON files. Files older than 30 days are automatically deleted on every run (both cron and chatbot). The chatbot's "View run by date" feature only shows results from the last 30 days.

---

## Google Chat Commands

| Command | Action |
|---|---|
| `menu` | Show main menu |
| `1` | Run normal flow |
| `2` | Run special request |
| `3` | Show latest counts |
| `4` | Show latest PR details |
| `5` | View run by date |

---

## Special Requests

To run a special request for a pilot, upload a CSV file to:
```
s3://bidgely-artifacts2/Murali_Users/special/<pilot>/<YYYYMMDD>/<request_name>.csv
```

The cron will automatically detect it and run the special request flow before the normal daily flow.

CSV format:
```
meterid
meter1
meter2
...
```
