import argparse
import math
from datetime import datetime, timedelta, timezone

import boto3

from config import (
    BASE_PREFIX,
    BUCKET,
    PILOT_AWS_PROFILES,
    PILOT_DB_CONFIGS,
    PILOT_DB_NETWORK_OVERRIDES,
    PILOT_DB_SECRET_ARNS,
    PILOT_EXPORT_AWS_PROFILES,
    PILOT_HOURLY_TRIGGER_THRESHOLDS,
    PILOT_NAMES,
    PILOT_REPORT_VARIANTS,
    PILOT_S3_PREFIXES,
    PILOT_WEATHER_CONFIGS,
)
from db.db_client import DBClient
from main import resolve_export_target, weather_data_available
from notifier.google_chat_sender import GoogleChatSender
from s3.s3_client import S3Client
from utils.logger import get_logger

logger = get_logger("WeatherHourlyNotifier")
IST = timezone(timedelta(hours=5, minutes=30))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot_ids", type=int, nargs="+", help="Pilot ids to check (default: all pilots in config)")
    parser.add_argument(
        "--target_datetime",
        type=str,
        help="Optional UTC datetime override in YYYY-MM-DDTHH format. Previous hour of current UTC time is used when omitted.",
    )
    return parser.parse_args()


def resolve_target_window(target_datetime_arg=None):
    if target_datetime_arg:
        target_dt = datetime.strptime(target_datetime_arg, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    else:
        target_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return target_dt.strftime("%Y-%m-%d"), target_dt.hour


def build_boto3_session(profile_name=None):
    if profile_name:
        logger.info("Using AWS profile '%s'", profile_name)
        return boto3.Session(profile_name=profile_name)
    return boto3.Session()


def list_weather_postal_codes(s3_client, weather_bucket, weather_prefix):
    paginator = s3_client.get_paginator("list_objects_v2")
    prefix = weather_prefix.rstrip("/") + "/"
    postal_codes = set()

    for page in paginator.paginate(Bucket=weather_bucket, Prefix=prefix, Delimiter="/"):
        for common_prefix in page.get("CommonPrefixes", []):
            value = common_prefix["Prefix"][len(prefix):].strip("/")
            if value.startswith("zipcode="):
                postal_code = value.split("=", 1)[1].strip()
                if postal_code:
                    postal_codes.add(postal_code)

    return sorted(postal_codes)


def check_solar_for_pilot(pilot, dt, hour, variant=None):
    from pilots.loader import load_pilot_configs
    variant = variant or {}
    _cfg = load_pilot_configs()
    aws_profile = _cfg["PILOT_AWS_PROFILES"].get(pilot)
    pilot_secret_arn = _cfg["PILOT_DB_SECRET_ARNS"].get(pilot)
    pilot_db_config = _cfg["PILOT_DB_CONFIGS"].get(pilot)
    pilot_db_network_override = _cfg["PILOT_DB_NETWORK_OVERRIDES"].get(pilot)

    try:
        db = DBClient(
            pilot,
            secret_arn=pilot_secret_arn,
            db_config=pilot_db_config,
            network_override=pilot_db_network_override,
            aws_profile=aws_profile,
        )
        expected_users = db.fetch_solar_users()
        db.close()
    except Exception as exc:
        return {
            "pilot": pilot,
            "status": "failed",
            "message": f"Solar DB lookup failed: {exc}",
        }

    aws_session = build_boto3_session(aws_profile)
    pilot_s3 = variant.get("s3") or PILOT_S3_PREFIXES.get(pilot, {})
    bucket = pilot_s3.get("bucket", BUCKET)
    base_prefix = pilot_s3.get("base_prefix", BASE_PREFIX)
    hour_prefix = f"{base_prefix.rstrip('/')}/date={dt}/hour={hour:02d}/"
    s3_client = S3Client(dt, dt, bucket=bucket, session=aws_session)

    try:
        present_users = sorted(s3_client.read_hour_data(hour_prefix))
    except Exception as exc:
        return {
            "pilot": pilot,
            "status": "failed",
            "message": f"Solar S3 read failed: {exc}",
        }

    expected_sorted = sorted(expected_users)
    expected_set = set(expected_sorted)
    present_set = set(present_users)
    missing_users = sorted(expected_set - present_set)

    return {
        "pilot": pilot,
        "variant_name": variant.get("name") or PILOT_NAMES.get(pilot, str(pilot)),
        "export_s3": variant.get("export_s3"),
        "status": "ok",
        "expected_count": len(expected_sorted),
        "present_count": len(present_users),
        "missing_count": len(missing_users),
        "present_users": present_users,
        "missing_users": missing_users,
        "date": dt,
        "hour": hour,
    }


def check_weather_for_pilot(pilot, dt, hour, variant=None):
    from pilots.loader import load_pilot_configs
    variant = variant or {}
    _cfg = load_pilot_configs()
    weather_config = _cfg["PILOT_WEATHER_CONFIGS"].get(pilot)
    if not weather_config:
        return {
            "pilot": pilot,
            "status": "skipped",
            "message": "No weather configuration found.",
        }

    aws_session = build_boto3_session(_cfg["PILOT_AWS_PROFILES"].get(pilot))
    s3_client = aws_session.client("s3")

    try:
        postal_codes = list_weather_postal_codes(
            s3_client,
            weather_config["weather_bucket"],
            weather_config["weather_prefix"],
        )
    except Exception as exc:
        return {
            "pilot": pilot,
            "status": "failed",
            "message": f"Weather zipcode discovery failed: {exc}",
        }

    if not postal_codes:
        return {
            "pilot": pilot,
            "status": "failed",
            "message": "No postal codes found in weather-data bucket.",
        }

    missing_postal_codes = []
    present_postal_codes = []
    for postal_code in postal_codes:
        if not weather_data_available(
            s3_client,
            weather_config["weather_bucket"],
            weather_config["weather_prefix"],
            postal_code,
            dt,
            hour,
        ):
            missing_postal_codes.append(postal_code)
        else:
            present_postal_codes.append(postal_code)

    return {
        "pilot": pilot,
        "variant_name": variant.get("name") or PILOT_NAMES.get(pilot, str(pilot)),
        "status": "ok",
        "postal_code_count": len(postal_codes),
        "present_postal_codes": present_postal_codes,
        "missing_postal_codes": missing_postal_codes,
        "date": dt,
        "hour": hour,
    }


def should_notify_for_pilot(result):
    from pilots.loader import load_pilot_configs
    pilot = result["pilot"]
    solar = result["solar"]
    weather = result["weather"]
    threshold = load_pilot_configs()["PILOT_HOURLY_TRIGGER_THRESHOLDS"].get(pilot, {"mode": "count", "value": 1})

    if solar["status"] != "ok":
        return True

    if weather["status"] != "ok":
        return True

    if weather.get("missing_postal_codes"):
        return True

    missing_count = solar.get("missing_count", 0)
    expected_count = solar.get("expected_count", 0)
    mode = threshold.get("mode", "count")
    value = threshold.get("value", 1)

    if mode == "percent":
        if expected_count <= 0:
            return False
        required_missing = math.ceil(expected_count * value)
        return missing_count >= required_missing

    return missing_count >= value


def upload_hourly_missing_uuid_files(results, dt, hour):
    uploaded_paths = {}

    for result in results:
        pilot = result["pilot"]
        solar = result["solar"]
        if solar.get("status") != "ok":
            continue

        missing_users = solar.get("missing_users") or []
        if not missing_users:
            continue

        export_s3_uri = result.get("export_s3")
        target = resolve_export_target(dt, pilot=pilot, export_s3_uri=export_s3_uri)
        if not target:
            continue
        bucket, date_prefix = target
        variant_name = result.get("variant_name") or PILOT_NAMES.get(pilot, str(pilot))
        variant_token = "_".join(variant_name.lower().split())
        key = f"{date_prefix}hour={hour:02d}/pilot_{pilot}_{variant_token}_missing_uuids.txt"

        export_profile = PILOT_EXPORT_AWS_PROFILES.get(pilot)
        export_session = build_boto3_session(export_profile)
        s3_client = export_session.client("s3")

        try:
            body = "\n".join(missing_users) + "\n"
            s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="text/plain",
            )
            s3_uri = f"s3://{bucket}/{key}"
            uploaded_paths[result.get("report_key", pilot)] = s3_uri
            logger.info(
                "Uploaded %d missing UUIDs for pilot %s to %s",
                len(missing_users),
                pilot,
                s3_uri,
            )
        except Exception as exc:
            logger.error(
                "Failed to upload hourly missing UUIDs for pilot %s to s3://%s/%s: %s",
                pilot,
                bucket,
                key,
                exc,
            )

    return uploaded_paths


def build_message(
    results,
    dt,
    hour,
    display_dt=None,
    display_hour=None,
    time_label="UTC",
    secondary_display_dt=None,
    secondary_display_hour=None,
    secondary_time_label=None,
    uploaded_paths=None,
):
    heading_prefix = "🔷 "
    uploaded_paths = uploaded_paths or {}
    lines = [
        "Hourly Solar and Weather Report",
        "-------------------------------",
        "",
        f"{heading_prefix}Target date: {display_dt or dt}",
        f"{heading_prefix}Target hour: {(display_hour if display_hour is not None else hour):02d} {time_label}",
        "",
    ]
    if secondary_time_label and secondary_display_hour is not None:
        lines.insert(5, f"{heading_prefix}Target date ({secondary_time_label}): {secondary_display_dt or dt}")
        lines.insert(6, f"{heading_prefix}Target hour ({secondary_time_label}): {secondary_display_hour:02d}")
        lines.insert(7, "")

    rows = []

    for result in results:
        pilot = result["pilot"]
        pilot_name = result.get("variant_name") or PILOT_NAMES.get(pilot, str(pilot))
        solar = result["solar"]
        weather = result["weather"]
        if solar["status"] == "ok":
            solar_expected = solar["expected_count"]
            solar_present = solar["present_count"]
            solar_missing = solar["missing_count"]
        else:
            solar_expected = "ERR"
            solar_present = "ERR"
            solar_missing = "ERR"

        if weather["status"] == "ok":
            weather_total = weather["postal_code_count"]
            weather_present = len(weather["present_postal_codes"])
            weather_missing = len(weather["missing_postal_codes"])
        else:
            weather_total = "ERR"
            weather_present = "ERR"
            weather_missing = "ERR"

        rows.append(
            [
                pilot_name,
                str(solar_expected),
                str(solar_present),
                str(solar_missing),
                str(weather_total),
                str(weather_present),
                str(weather_missing),
            ]
        )

    lines.extend([
        "Legend:",
        "Exp = Expected, Sol Pres = Solar Present, Sol Miss = Solar Missing",
        "W Tot = Weather Total, W Pres = Weather Present, W Miss = Weather Missing",
        "",
    ])

    headers = [
        "Pilot",
        "Exp",
        "Sol Pres",
        "Sol Miss",
        "W Tot",
        "W Pres",
        "W Miss",
    ]
    column_widths = [
        max(len(header), max((len(row[idx]) for row in rows), default=0))
        for idx, header in enumerate(headers)
    ]

    def format_row(values):
        return "  ".join(value.ljust(column_widths[idx]) for idx, value in enumerate(values))

    table_lines = [format_row(headers)]
    table_lines.append("  ".join("-" * width for width in column_widths))
    for row in rows:
        table_lines.append(format_row(row))
    lines.append("```")
    lines.extend(table_lines)
    lines.append("```")

    location_lines = []
    for result in results:
        pilot = result["pilot"]
        path = uploaded_paths.get(result.get("report_key", pilot))
        if not path:
            continue
        pilot_name = result.get("variant_name") or PILOT_NAMES.get(pilot, str(pilot))
        location_lines.append(f"{heading_prefix}{pilot_name} missing UUID file: {path}")

    if location_lines:
        lines.append("")
        lines.extend(location_lines)

    return "\n".join(lines).rstrip()


def main():
    args = parse_args()
    dt, hour = resolve_target_window(args.target_datetime)
    target_dt_utc = datetime.strptime(f"{dt}T{hour:02d}", "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    target_dt_ist = target_dt_utc.astimezone(IST)

    from pilots.loader import load_pilot_configs
    pilot_ids = args.pilot_ids or sorted(load_pilot_configs()["PILOT_NAMES"].keys())
    logger.info("Running for pilots: %s", pilot_ids)

    results = []
    for pilot in pilot_ids:
        variants = PILOT_REPORT_VARIANTS.get(pilot) or [{"name": PILOT_NAMES.get(pilot, str(pilot))}]
        for variant in variants:
            variant_name = variant.get("name") or PILOT_NAMES.get(pilot, str(pilot))
            results.append(
                {
                    "pilot": pilot,
                    "variant_name": variant_name,
                    "export_s3": variant.get("export_s3"),
                    "report_key": f"{pilot}:{variant_name}",
                    "solar": check_solar_for_pilot(pilot, dt, hour, variant=variant),
                    "weather": check_weather_for_pilot(pilot, dt, hour, variant=variant),
                }
            )
    results_to_notify = [result for result in results if should_notify_for_pilot(result)]
    if not results_to_notify:
        logger.info(
            "No hourly notification sent for date=%s hour=%02d because no pilot crossed its trigger threshold.",
            dt,
            hour,
        )
        return
    uploaded_paths = upload_hourly_missing_uuid_files(results_to_notify, dt, hour)
    message = build_message(
        results_to_notify,
        dt,
        hour,
        display_dt=target_dt_utc.strftime("%Y-%m-%d"),
        display_hour=target_dt_utc.hour,
        time_label="UTC",
        secondary_display_dt=target_dt_ist.strftime("%Y-%m-%d"),
        secondary_display_hour=target_dt_ist.hour,
        secondary_time_label="IST",
        uploaded_paths=uploaded_paths,
    )

    gchat = GoogleChatSender()
    gchat.send_text(message)
    logger.info("Hourly weather notification processed for date=%s hour=%02d", dt, hour)


if __name__ == "__main__":
    main()
