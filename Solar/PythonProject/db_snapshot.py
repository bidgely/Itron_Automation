import argparse
import json
import os
import boto3
from datetime import date

from db.db_client import DBClient
from config import (
    PILOT_DB_CONFIGS,
    PILOT_DB_NETWORK_OVERRIDES,
    PILOT_DB_SECRET_ARNS,
    PILOT_AWS_PROFILES,
    PILOT_EXPORT_AWS_PROFILES,
    PILOT_NAMES,
)
from utils.logger import get_logger

logger = get_logger("DBSnapshot")

SNAPSHOT_BUCKET = os.getenv("SNAPSHOT_BUCKET", "bidgely-artifacts2")
SNAPSHOT_PREFIX = os.getenv("SNAPSHOT_PREFIX", "Murali_Users/db_snapshots")


def save_snapshot(pilot_id, date_str, users, s3_client):
    key = f"{SNAPSHOT_PREFIX}/pilot_{pilot_id}/date={date_str}/users.json"
    data = {
        "pilot_id": pilot_id,
        "pilot_name": PILOT_NAMES.get(pilot_id),
        "date": date_str,
        "user_count": len(users),
        "users": sorted(users),
    }
    s3_client.put_object(
        Bucket=SNAPSHOT_BUCKET,
        Key=key,
        Body=json.dumps(data).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        "Saved %d users for pilot %s on %s → s3://%s/%s",
        len(users), pilot_id, date_str, SNAPSHOT_BUCKET, key,
    )
    return f"s3://{SNAPSHOT_BUCKET}/{key}"


def snapshot_pilot(pilot_id, date_str):
    aws_profile = PILOT_AWS_PROFILES.get(pilot_id)
    export_profile = PILOT_EXPORT_AWS_PROFILES.get(pilot_id)
    export_session = boto3.Session(profile_name=export_profile) if export_profile else boto3.Session()
    s3_client = export_session.client("s3")

    db = DBClient(
        pilot_id,
        secret_arn=PILOT_DB_SECRET_ARNS.get(pilot_id),
        db_config=PILOT_DB_CONFIGS.get(pilot_id),
        network_override=PILOT_DB_NETWORK_OVERRIDES.get(pilot_id),
        aws_profile=aws_profile,
    )
    users = db.fetch_solar_users()
    db.close()
    return save_snapshot(pilot_id, date_str, users, s3_client)


def main():
    parser = argparse.ArgumentParser(description="Save daily DB snapshot of solar users to S3")
    parser.add_argument("--pilot_ids", type=int, nargs="+",
                        help="Pilot ids to snapshot (default: all pilots in config)")
    parser.add_argument("--date", type=str, default=date.today().strftime("%Y-%m-%d"),
                        help="Date in YYYY-MM-DD format (default: today)")
    args = parser.parse_args()

    from pilots.loader import load_pilot_configs
    pilot_ids = args.pilot_ids or sorted(load_pilot_configs()["PILOT_NAMES"].keys())
    logger.info("Snapshotting pilots: %s", pilot_ids)

    for pilot_id in pilot_ids:
        try:
            path = snapshot_pilot(pilot_id, args.date)
            logger.info("Snapshot complete for pilot %s: %s", pilot_id, path)
        except Exception as exc:
            logger.error("Failed to snapshot pilot %s: %s", pilot_id, exc)


if __name__ == "__main__":
    main()
