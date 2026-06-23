import argparse
import boto3
import glob
import random
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from urllib import request as urlrequest, error as urlerror
import json
from db.db_client import DBClient
from config import (
    BUCKET,
    BASE_PREFIX,
    PILOT_S3_PREFIXES,
    PILOT_EXPORT_S3,
    PILOT_REPORT_VARIANTS,
    PILOT_NAMES,
    PILOT_DB_CONFIGS,
    PILOT_DB_NETWORK_OVERRIDES,
    PILOT_DB_SECRET_ARNS,
    PILOT_AWS_PROFILES,
    PILOT_EXPORT_AWS_PROFILES,
    MISSING_EXPORT_ENABLED,
    MISSING_EXPORT_S3_URI,
    OUTPUT_DIR,
    CHART_IMAGE_BUCKET,
    CHART_IMAGE_PREFIX,
    CHART_CLOUDFRONT_BASE_URL,
    SQS_QUEUE_URL,
    SQS_REGION,
    USER_API_BASE_URL,
    USER_API_TOKEN_ENV,
    WEATHER_DATA_BUCKET,
    WEATHER_DATA_PREFIX,
    WEATHER_LOOKUP_MODE,
    WEATHER_REDSHIFT_HOST,
    WEATHER_REDSHIFT_PORT,
    WEATHER_REDSHIFT_DATABASE,
    WEATHER_REDSHIFT_USER,
    WEATHER_REDSHIFT_PASSWORD,
    WEATHER_REDSHIFT_QUERY_TEMPLATE,
    WEATHER_S3_CHECK_WORKERS,
    PILOT_WEATHER_CONFIGS,
)
from notifier.google_chat_sender import GoogleChatSender
from s3.s3_client import S3Client
from processor.data_processor import DataProcessor
from report.hourly_chart_generator import HourlyChartGenerator
from report.report_generator import ReportGenerator
from utils.logger import get_logger
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

logger = get_logger("Main")


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--pilot_id", type=int, help="Single pilot id (deprecated)")
    parser.add_argument("--pilot_ids", type=int, nargs="+", help="List of pilot ids to process")
    parser.add_argument("--start_date", type=str, required=True)
    parser.add_argument("--end_date", type=str, required=True)
    parser.add_argument("--test_mode", action="store_true")
    parser.add_argument("--weather_check", action="store_true", help="Enable weather-based missing-user reason classification")
    return parser.parse_args()


def build_boto3_session(profile_name=None):
    if profile_name:
        logger.info(f"Using AWS profile '{profile_name}'")
        return boto3.Session(profile_name=profile_name)
    return boto3.Session()


def fetch_queue_stats(queue_url, region, test_mode=False, session=None):
    if test_mode:
        return None
    try:
        boto_session = session or boto3.Session()
        sqs = boto_session.client("sqs", region_name=region)
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        visible = int(attrs.get("ApproximateNumberOfMessages", 0))
        inflight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
        return {"visible": visible, "inflight": inflight, "total": visible + inflight}
    except Exception as e:
        logger.warning(f"Skipping queue stats ({e})")
        return None


def main():
    args = parse_args()

    logger.info(f"Date Range: {args.start_date} → {args.end_date}")

    # 🔷 S3 + Processing
    report = ReportGenerator()
    chart_generator = HourlyChartGenerator()
    gchat = GoogleChatSender()

    # Determine pilots to process (pilot_ids take precedence)
    pilots = args.pilot_ids if getattr(args, "pilot_ids", None) else ([args.pilot_id] if getattr(args, "pilot_id", None) else None)
    if not pilots:
        from pilots.loader import load_pilot_configs
        pilots = sorted(load_pilot_configs()["PILOT_NAMES"].keys())
        logger.info("No --pilot_ids specified, running all pilots from config: %s", pilots)

    for pilot in pilots:
        logger.info(f"Processing pilot {pilot}")
        read_aws_profile = PILOT_AWS_PROFILES.get(pilot)
        write_aws_profile = PILOT_EXPORT_AWS_PROFILES.get(pilot)
        aws_session = build_boto3_session(read_aws_profile)
        export_session = build_boto3_session(write_aws_profile)
        export_s3_client = export_session.client("s3")
        pilot_weather_config = PILOT_WEATHER_CONFIGS.get(
            pilot,
            {
                "lookup_mode": WEATHER_LOOKUP_MODE,
                "api_base_url": USER_API_BASE_URL,
                "token_env": USER_API_TOKEN_ENV,
                "weather_bucket": WEATHER_DATA_BUCKET,
                "weather_prefix": WEATHER_DATA_PREFIX,
                "redshift_host": WEATHER_REDSHIFT_HOST,
                "redshift_port": WEATHER_REDSHIFT_PORT,
                "redshift_database": WEATHER_REDSHIFT_DATABASE,
                "redshift_user": WEATHER_REDSHIFT_USER,
                "redshift_password": WEATHER_REDSHIFT_PASSWORD,
                "redshift_query_template": WEATHER_REDSHIFT_QUERY_TEMPLATE,
            },
        )

        # 🔷 DB
        pilot_secret_arn = PILOT_DB_SECRET_ARNS.get(pilot)
        pilot_db_config = PILOT_DB_CONFIGS.get(pilot)
        pilot_db_network_override = PILOT_DB_NETWORK_OVERRIDES.get(pilot)
        db = DBClient(
            pilot,
            secret_arn=pilot_secret_arn,
            db_config=pilot_db_config,
            network_override=pilot_db_network_override,
            aws_profile=read_aws_profile,
        )
        expected_users = db.fetch_solar_users()
        db.close()

        queue_stats = fetch_queue_stats(SQS_QUEUE_URL, SQS_REGION, args.test_mode, session=aws_session)
        variants = PILOT_REPORT_VARIANTS.get(pilot) or [{"name": PILOT_NAMES.get(pilot)}]

        for variant in variants:
            variant_name = variant.get("name") or PILOT_NAMES.get(pilot)
            logger.info(f"Processing pilot {pilot} report variant: {variant_name}")

            # pilot-specific S3 settings, optionally overridden by a report variant
            pilot_s3 = variant.get("s3") or PILOT_S3_PREFIXES.get(pilot, {})
            bucket = pilot_s3.get("bucket", BUCKET)
            base_prefix = pilot_s3.get("base_prefix", BASE_PREFIX)
            export_s3_uri = variant.get("export_s3")

            s3_client = S3Client(
                args.start_date,
                args.end_date,
                args.test_mode,
                bucket=bucket,
                session=aws_session,
            )
            processor = DataProcessor(s3_client)

            for date_prefix in s3_client.list_prefixes(base_prefix):

                dt, daily_data, hourly_data = processor.process_date(date_prefix)

                if dt is None:
                    continue

                missing_reason_summary = build_disabled_missing_reason_summary(
                    "Weather check not enabled for this run."
                )
                if args.weather_check:
                    missing_reason_summary = classify_missing_users(
                        aws_session.client("s3"),
                        expected_users,
                        hourly_data,
                        dt,
                        pilot=pilot,
                        weather_config=pilot_weather_config,
                    )

                report.generate_hourly_report(
                    dt,
                    expected_users,
                    hourly_data,
                    queue_stats,
                    missing_reason_details=missing_reason_summary["hourly"] if args.weather_check else None,
                )
                stats = compute_hourly_stats(expected_users, hourly_data)
                summary_csv_path = report.generate_hourly_summary_csv(
                    dt,
                    stats["total_users"],
                    hourly_data,
                    missing_reason_details=missing_reason_summary["hourly"] if args.weather_check else None,
                )
                chart_local_path, chart_content_type = chart_generator.generate_hourly_chart_image(
                    dt,
                    stats["total_users"],
                    stats["present_counts"],
                    stats["missing_counts"],
                )
                report_s3_path = upload_hourly_user_files_to_s3(
                    export_s3_client,
                    dt,
                    pilot=pilot,
                    export_s3_uri=export_s3_uri,
                )
                csv_s3_path, csv_presigned_url = upload_csv_to_s3_and_get_url(
                    export_s3_client,
                    dt,
                    summary_csv_path,
                    pilot=pilot,
                    export_s3_uri=export_s3_uri,
                )
                chart_presigned_url = upload_chart_to_s3_and_get_url(
                    export_s3_client,
                    dt,
                    chart_local_path,
                    chart_content_type,
                    pilot=pilot,
                    variant_name=variant_name,
                )
                chart_preview_url = None
                if chart_presigned_url:
                    separator = "&" if "?" in chart_presigned_url else "?"
                    chart_preview_url = f"{chart_presigned_url}{separator}v={int(time.time())}"
                message_text = build_gchat_message(
                    pilot_id=pilot,
                    dt=dt,
                    stats=stats,
                    report_s3_path=report_s3_path,
                    csv_s3_path=csv_s3_path,
                    csv_presigned_url=csv_presigned_url,
                    pilot_name=variant_name,
                    missing_reason_summary=missing_reason_summary,
                )

                gchat.send_report(
                    message_text,
                    image_url=chart_preview_url,
                    csv_url=csv_presigned_url,
                    chart_url=chart_presigned_url,
                )

                # 🔥 MEMORY CLEANUP (CRITICAL for millions)
                del daily_data

    logger.info("✅ Audit Completed")


def parse_s3_uri(s3_uri):
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")
    return bucket, prefix


def upload_hourly_user_files_to_s3(s3_client, dt, pilot=None, export_s3_uri=None):
    if not MISSING_EXPORT_ENABLED:
        logger.info("Hourly user-file export disabled. Skipping S3 upload.")
        return None

    local_date_dir = os.path.join(OUTPUT_DIR, dt)
    file_patterns = [
        os.path.join(local_date_dir, "hour_*_missing.txt"),
        os.path.join(local_date_dir, "hour_*_present.txt"),
    ]
    user_files = sorted(
        {
            path
            for pattern in file_patterns
            for path in glob.glob(pattern)
        }
    )
    if not user_files:
        logger.warning(f"No hourly user files found for upload in {local_date_dir}")
        return None

    target = resolve_export_target(dt, pilot=pilot, export_s3_uri=export_s3_uri)
    if not target:
        return None
    bucket, date_prefix = target

    uploaded = 0
    for local_path in user_files:
        filename = os.path.basename(local_path)
        key = f"{date_prefix}{filename}"
        try:
            s3_client.upload_file(local_path, bucket, key)
            uploaded += 1
        except Exception as exc:
            logger.error(f"Failed to upload {local_path} to s3://{bucket}/{key}: {exc}")

    logger.info(
        f"Uploaded {uploaded}/{len(user_files)} hourly user files to "
        f"s3://{bucket}/{date_prefix}"
    )
    return f"s3://{bucket}/{date_prefix}"


def resolve_export_target(dt, pilot=None, export_s3_uri=None):
    # Per-pilot override if configured
    if export_s3_uri:
        s3_uri = export_s3_uri
    elif pilot is not None and pilot in PILOT_EXPORT_S3:
        s3_uri = PILOT_EXPORT_S3[pilot]
    else:
        s3_uri = MISSING_EXPORT_S3_URI

    try:
        bucket, base_prefix = parse_s3_uri(s3_uri)
    except Exception as exc:
        logger.error(f"Invalid export S3 URI '{s3_uri}': {exc}")
        return None
    date_prefix = f"{base_prefix.rstrip('/')}/date={dt}/" if base_prefix else f"date={dt}/"
    return bucket, date_prefix


def upload_chart_to_s3_and_get_url(s3_client, dt, local_chart_path, content_type, pilot=None, variant_name=None):
    if not MISSING_EXPORT_ENABLED or not local_chart_path or not os.path.exists(local_chart_path):
        return None

    ext = ".png" if content_type == "image/png" else ".svg"
    pilot_token = str(pilot) if pilot is not None else "shared"
    if variant_name:
        variant_token = re.sub(r"[^A-Za-z0-9]+", "_", variant_name).strip("_").lower()
        pilot_token = f"{pilot_token}_{variant_token}"
    run_token = int(time.time() * 1000)
    filename = f"pilot_{pilot_token}_{dt}_{run_token}_hourly_present_absent{ext}"
    key_prefix = CHART_IMAGE_PREFIX.strip("/")
    key = f"{key_prefix}/{filename}" if key_prefix else filename
    bucket = CHART_IMAGE_BUCKET
    cloudfront_base = CHART_CLOUDFRONT_BASE_URL.rstrip("/")
    cloudfront_url = f"{cloudfront_base}/{key}"

    try:
        s3_client.upload_file(
            local_chart_path,
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        logger.info(f"Uploaded chart to s3://{bucket}/{key}")
        logger.info(f"Chart CloudFront URL: {cloudfront_url}")
        return cloudfront_url
    except Exception as exc:
        logger.error(f"Failed to upload chart to s3://{bucket}/{key}: {exc}")
        return None


def upload_csv_to_s3_and_get_url(s3_client, dt, local_csv_path, pilot=None, export_s3_uri=None):
    if not MISSING_EXPORT_ENABLED or not local_csv_path or not os.path.exists(local_csv_path):
        return None, None

    target = resolve_export_target(dt, pilot=pilot, export_s3_uri=export_s3_uri)
    if not target:
        return None, None
    bucket, date_prefix = target
    key = f"{date_prefix}hourly_summary.csv"

    try:
        s3_client.upload_file(
            local_csv_path,
            bucket,
            key,
            ExtraArgs={"ContentType": "text/csv"},
        )
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=7 * 24 * 3600,
        )
        s3_path = f"s3://{bucket}/{key}"
        logger.info(f"Uploaded hourly CSV to {s3_path}")
        return s3_path, url
    except Exception as exc:
        logger.error(f"Failed to upload hourly CSV to s3://{bucket}/{key}: {exc}")
        return None, None


def compute_hourly_stats(expected_users, hourly_data):
    total_users = len(expected_users)
    missing_hours = []
    full_hours = []
    present_counts = []
    missing_counts = []

    for hour in range(24):
        present_count = len(hourly_data.get(hour, set()))
        missing_count = max(0, total_users - present_count)
        present_counts.append(present_count)
        missing_counts.append(missing_count)
        if present_count == total_users:
            full_hours.append(hour)
        else:
            missing_hours.append(hour)

    return {
        "total_users": total_users,
        "missing_hours": missing_hours,
        "full_hours": full_hours,
        "present_counts": present_counts,
        "missing_counts": missing_counts,
    }


def normalize_postal_code(value):
    if value is None:
        return None
    postal_code = str(value).strip()
    if not postal_code:
        return None
    postal_code = postal_code.split("-")[0].strip()
    return postal_code[:5] if postal_code else None


def fetch_user_postal_code(uuid, api_base_url, token):
    url = api_base_url.rstrip("/") + "/" + uuid
    req = urlrequest.Request(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )

    with urlrequest.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        data = json.loads(body)

    payload = data.get("payload") or {}
    if isinstance(payload, dict):
        home_accounts = payload.get("homeAccounts") or {}
        if isinstance(home_accounts, dict):
            postal = home_accounts.get("postalCode") or home_accounts.get("postal_code")
            if postal:
                return normalize_postal_code(postal)

    postal = data.get("postalCode") or data.get("postal_code") or data.get("zipcode") or data.get("zip")
    return normalize_postal_code(postal)


def fetch_pilot_postal_codes_from_redshift(pilot, weather_config):
    psql_path = shutil.which("psql")
    if not psql_path:
        raise RuntimeError("psql is not installed on this machine")

    host = weather_config.get("redshift_host")
    user = weather_config.get("redshift_user")
    password = weather_config.get("redshift_password")
    database = weather_config.get("redshift_database")
    port = weather_config.get("redshift_port")
    query_template = weather_config.get("redshift_query_template")

    missing_fields = [
        name
        for name, value in (
            ("redshift_host", host),
            ("redshift_user", user),
            ("redshift_password", password),
            ("redshift_database", database),
            ("redshift_query_template", query_template),
        )
        if not value
    ]
    if missing_fields:
        raise RuntimeError(
            "Missing Redshift weather config: " + ", ".join(sorted(missing_fields))
        )

    query = query_template.format(pilot_id=pilot)
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    result = subprocess.run(
        [
            psql_path,
            "-X",
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=60,
    )

    postal_codes = sorted(
        {
            normalize_postal_code(line)
            for line in result.stdout.splitlines()
            if normalize_postal_code(line)
        }
    )
    return postal_codes


def weather_data_available(s3_client, weather_bucket, weather_prefix_base, postal_code, dt, hour):
    prefix = f"{weather_prefix_base.rstrip('/')}/zipcode={postal_code}/date={dt}/hour={hour:02d}/"
    response = s3_client.list_objects_v2(Bucket=weather_bucket, Prefix=prefix, MaxKeys=1)
    return bool(response.get("KeyCount", 0) > 0 or response.get("Contents"))


def classify_missing_users_with_redshift(
    s3_client,
    expected_users,
    hourly_data,
    dt,
    pilot,
    weather_config,
):
    reason_logger = get_logger("MissingReason")
    reason_names = (
        "weather_data_missing",
        "weather_data_present_but_solar_missing",
        "postal_missing",
        "user_lookup_failed",
    )
    hourly_summary = {
        hour: {reason: [] for reason in reason_names}
        for hour in range(24)
    }

    try:
        postal_codes = fetch_pilot_postal_codes_from_redshift(pilot, weather_config)
    except Exception as exc:
        message = f"Weather analysis skipped (Redshift lookup failed: {exc})"
        reason_logger.warning(message)
        return {
            "enabled": False,
            "message": message,
            "source": "redshift",
            "postal_code_count": 0,
            "hourly": hourly_summary,
            "totals": {reason: 0 for reason in reason_names},
            "hourly_scope": {},
        }

    if not postal_codes:
        message = f"Weather analysis skipped (no postal codes found in Redshift for pilot {pilot})."
        reason_logger.warning(message)
        return {
            "enabled": False,
            "message": message,
            "source": "redshift",
            "postal_code_count": 0,
            "hourly": hourly_summary,
            "totals": {reason: 0 for reason in reason_names},
            "hourly_scope": {},
        }

    weather_bucket = weather_config["weather_bucket"]
    weather_prefix_base = weather_config["weather_prefix"]
    weather_cache = {}
    hourly_scope = {}
    hours_to_check = [
        hour
        for hour in range(24)
        if expected_users - hourly_data.get(hour, set())
    ]

    worker_count = min(
        WEATHER_S3_CHECK_WORKERS,
        max(1, len(postal_codes) * max(1, len(hours_to_check))),
    )

    lookup_pairs = [
        (postal_code, hour)
        for hour in hours_to_check
        for postal_code in postal_codes
    ]

    if lookup_pairs:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_pair = {
                executor.submit(
                    weather_data_available,
                    s3_client,
                    weather_bucket,
                    weather_prefix_base,
                    postal_code,
                    dt,
                    hour,
                ): (postal_code, hour)
                for postal_code, hour in lookup_pairs
            }
            for future in as_completed(future_to_pair):
                postal_code, hour = future_to_pair[future]
                weather_cache[(postal_code, hour)] = future.result()

    for hour in range(24):
        population_size = len(postal_codes)
        missing_postal_codes = []
        if hour in hours_to_check:
            for postal_code in postal_codes:
                cache_key = (postal_code, hour)
                if not weather_cache.get(cache_key):
                    missing_postal_codes.append(postal_code)

        hourly_summary[hour]["weather_data_missing"] = missing_postal_codes
        hourly_scope[hour] = {
            "sampled": False,
            "sample_size": population_size,
            "population_size": population_size,
        }

    totals = {
        reason: sum(len(hourly_summary[hour][reason]) for hour in range(24))
        for reason in reason_names
    }

    return {
        "enabled": True,
        "message": None,
        "source": "redshift",
        "postal_code_count": len(postal_codes),
        "hourly": hourly_summary,
        "totals": totals,
        "hourly_scope": hourly_scope,
    }


def classify_missing_users_with_api(
    s3_client,
    expected_users,
    hourly_data,
    dt,
    api_base_url,
    token_env,
    weather_bucket,
    weather_prefix_base,
):
    reason_logger = get_logger("MissingReason")
    token = os.getenv(token_env)
    api_fetch_workers = 25
    reason_names = (
        "weather_data_missing",
        "weather_data_present_but_solar_missing",
        "postal_missing",
        "user_lookup_failed",
    )

    hourly_summary = {
        hour: {reason: [] for reason in reason_names}
        for hour in range(24)
    }

    if not token:
        message = f"Reason classification skipped ({token_env} not configured)."
        reason_logger.warning(message)
        return {
            "enabled": False,
            "message": message,
            "source": "api",
            "postal_code_count": 0,
            "hourly": hourly_summary,
            "totals": {reason: 0 for reason in reason_names},
            "hourly_scope": {},
        }

    user_info_cache = {}
    weather_cache = {}
    hourly_scope = {}

    for hour in range(24):
        missing_for_hour = sorted(expected_users - hourly_data.get(hour, set()))
        population_size = len(missing_for_hour)
        users_to_check = missing_for_hour
        reason_logger.info(
            "Using full missing-user classification for hour %02d: %s users",
            hour,
            population_size,
        )

        hourly_scope[hour] = {
            "sampled": False,
            "sample_size": len(users_to_check),
            "population_size": population_size,
        }

        uncached_users = [uuid for uuid in users_to_check if uuid not in user_info_cache]
        if uncached_users:
            with ThreadPoolExecutor(max_workers=min(api_fetch_workers, len(uncached_users))) as executor:
                future_to_uuid = {
                    executor.submit(fetch_user_postal_code, uuid, api_base_url, token): uuid
                    for uuid in uncached_users
                }
                for future in as_completed(future_to_uuid):
                    uuid = future_to_uuid[future]
                    try:
                        user_info_cache[uuid] = {
                            "postal_code": future.result(),
                            "lookup_failed": False,
                        }
                    except Exception as exc:
                        reason_logger.warning(f"Failed to fetch postal code for {uuid}: {exc}")
                        user_info_cache[uuid] = {
                            "postal_code": None,
                            "lookup_failed": True,
                        }

        for uuid in users_to_check:
            user_info = user_info_cache.get(uuid, {})
            postal_code = user_info.get("postal_code")

            if user_info.get("lookup_failed"):
                hourly_summary[hour]["user_lookup_failed"].append(uuid)
                continue

            if not postal_code:
                hourly_summary[hour]["postal_missing"].append(uuid)
                continue

            cache_key = (postal_code, hour)
            if cache_key not in weather_cache:
                try:
                    weather_cache[cache_key] = weather_data_available(
                        s3_client,
                        weather_bucket,
                        weather_prefix_base,
                        postal_code,
                        dt,
                        hour,
                    )
                except Exception as exc:
                    reason_logger.warning(
                        f"Failed weather lookup for zipcode={postal_code}, hour={hour:02d}: {exc}"
                    )
                    weather_cache[cache_key] = None

            if weather_cache[cache_key] is None:
                hourly_summary[hour]["user_lookup_failed"].append(uuid)
            elif weather_cache[cache_key]:
                hourly_summary[hour]["weather_data_present_but_solar_missing"].append(uuid)
            else:
                hourly_summary[hour]["weather_data_missing"].append(uuid)

    totals = {
        reason: sum(len(hourly_summary[hour][reason]) for hour in range(24))
        for reason in reason_names
    }

    return {
        "enabled": True,
        "message": None,
        "source": "api",
        "postal_code_count": 0,
        "hourly": hourly_summary,
        "totals": totals,
        "hourly_scope": hourly_scope,
    }


def classify_missing_users(
    s3_client,
    expected_users,
    hourly_data,
    dt,
    pilot,
    weather_config,
):
    lookup_mode = (weather_config.get("lookup_mode") or "api").lower()
    if lookup_mode == "redshift":
        return classify_missing_users_with_redshift(
            s3_client,
            expected_users,
            hourly_data,
            dt,
            pilot,
            weather_config,
        )

    return classify_missing_users_with_api(
        s3_client,
        expected_users,
        hourly_data,
        dt,
        api_base_url=weather_config["api_base_url"],
        token_env=weather_config["token_env"],
        weather_bucket=weather_config["weather_bucket"],
        weather_prefix_base=weather_config["weather_prefix"],
    )


def build_disabled_missing_reason_summary(message):
    return {
        "enabled": False,
        "message": message,
        "source": "disabled",
        "postal_code_count": 0,
        "hourly": {},
        "totals": {},
        "hourly_scope": {},
    }


def build_gchat_message(
    pilot_id,
    dt,
    stats,
    report_s3_path=None,
    csv_s3_path=None,
    csv_presigned_url=None,
    pilot_name=None,
    missing_reason_summary=None,
):
    total_users = stats["total_users"]
    present_counts = stats["present_counts"]
    missing_counts = stats["missing_counts"]
    report_path = report_s3_path or "Not uploaded"

    # Build hourly lines without extra blank lines between them per user request
    hour_lines = []
    for hour in range(24):
        present = present_counts[hour] if hour < len(present_counts) else 0
        missing = missing_counts[hour] if hour < len(missing_counts) else total_users
        hour_lines.append(
            f"Hour {hour:02d} -> Total: {total_users}, Present: {present}, Missing: {missing}"
        )
    hour_block = "\n".join(hour_lines)

    # Use simple Markdown-like formatting supported by Google Chat: *bold* for headings.
    title = "Solar Audit Daily Report\n" + "---------------------------\n\n"
    # Use a colored emoji as a visual "color" for side headings since Google Chat doesn't support text color
    heading_prefix = "🔷 "

    header_lines = [
        f"{heading_prefix}Pilot ID: {pilot_id}",
    ]
    if pilot_name:
        header_lines.append(f"{heading_prefix}Pilot Name: {pilot_name}")
    header_lines.extend([
        f"{heading_prefix}Date: {dt}",
        f"{heading_prefix}Expected users: {total_users}",
        "",
    ])
    header = "\n".join(header_lines) + "\n\n"

    hourly_heading = f"{heading_prefix}Hourly Stats:\n"

    # Column header with side headings and pipe-separated columns
    column_header = f"{heading_prefix}Hour | Total | Present | Missing\n"

    graph = f"\n{heading_prefix}Graph: Present (green), Absent (red), X=Hour, Y=User Count\n\n"

    # Make the CSV download URL clickable in the card. Keep other labels plain.
    if csv_presigned_url:
        csv_link = f'<a href="{csv_presigned_url}">Download CSV</a>'
    else:
        csv_link = 'Not available'

    paths = (
        f"{heading_prefix}Missing Users Report path: {report_path}\n"
        f"{heading_prefix}CSV path: {csv_s3_path or 'Not uploaded'}\n"
        f"{heading_prefix}CSV download URL: {csv_link}"
    )

    reason_block = build_missing_reason_block(missing_reason_summary, heading_prefix)

    return title + header + hourly_heading + column_header + hour_block + graph + paths + reason_block


def build_missing_reason_block(missing_reason_summary, heading_prefix):
    if not missing_reason_summary:
        return ""

    if not missing_reason_summary.get("enabled"):
        return (
            f"\n\n{heading_prefix}Missing Reason Summary:\n"
            f"{missing_reason_summary.get('message', 'Unavailable')}"
        )

    if missing_reason_summary.get("source") == "redshift":
        lines = [
            "",
            f"{heading_prefix}Weather Data Summary:",
            f"Postal codes checked: {missing_reason_summary.get('postal_code_count', 0)}",
        ]
        any_missing = False
        for hour in range(24):
            missing_zipcodes = missing_reason_summary["hourly"].get(hour, {}).get("weather_data_missing", [])
            if not missing_zipcodes:
                continue
            any_missing = True
            lines.append(
                f"Hour {hour:02d} -> Weather data not available for {len(missing_zipcodes)} postal codes"
            )
        if not any_missing:
            lines.append("Weather data available for all postal codes checked.")
        return "\n\n" + "\n".join(lines)

    totals = missing_reason_summary.get("totals", {})
    lines = [
        "",
        f"{heading_prefix}Missing Reason Summary:",
        (
            f"Total missing events by reason -> "
            f"weather_data_missing: {totals.get('weather_data_missing', 0)}, "
            f"weather_data_present_but_solar_missing: {totals.get('weather_data_present_but_solar_missing', 0)}, "
            f"postal_missing: {totals.get('postal_missing', 0)}, "
            f"user_lookup_failed: {totals.get('user_lookup_failed', 0)}"
        ),
    ]

    for hour in range(24):
        scope = missing_reason_summary.get("hourly_scope", {}).get(hour, {})
        reason_counts = {
            reason: len(users)
            for reason, users in missing_reason_summary["hourly"].get(hour, {}).items()
            if users
        }
        if not reason_counts:
            continue
        lines.append(
            f"Hour {hour:02d} "
            f"(full: {scope.get('population_size', 0)}) -> "
            + ", ".join(f"{reason}: {count}" for reason, count in sorted(reason_counts.items()))
        )

    return "\n\n" + "\n".join(lines)


if __name__ == "__main__":
    main()
