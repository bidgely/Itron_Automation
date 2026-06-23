import json
import os
import sys
import time
import urllib.parse
from datetime import date, timedelta, datetime, timezone
from urllib import request as urlrequest

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    BUCKET,
    BASE_PREFIX,
    MISSING_EXPORT_ENABLED,
)
from pilots.loader import load_pilot_configs
from db.db_client import DBClient
from s3.s3_client import S3Client
from processor.data_processor import DataProcessor
from report.report_generator import ReportGenerator
from report.hourly_chart_generator import HourlyChartGenerator
from main import (
    compute_hourly_stats,
    upload_chart_to_s3_and_get_url,
    upload_csv_to_s3_and_get_url,
    upload_hourly_user_files_to_s3,
    build_gchat_message,
    build_boto3_session,
)
from weather_hourly_notifier import (
    check_solar_for_pilot,
    check_weather_for_pilot,
    resolve_target_window,
)
from utils.logger import get_logger

logger = get_logger("ChatbotService")

SNAPSHOT_BUCKET = os.getenv("SNAPSHOT_BUCKET", "bidgely-artifacts2")
SNAPSHOT_PREFIX = os.getenv("SNAPSHOT_PREFIX", "Murali_Users/db_snapshots")
IST = timezone(timedelta(hours=5, minutes=30))


# ─── Snapshot helpers ────────────────────────────────────────────────────────

def load_expected_users(pilot_id, date_str):
    cfg = load_pilot_configs()
    key = f"{SNAPSHOT_PREFIX}/pilot_{pilot_id}/date={date_str}/users.json"
    export_profile = cfg["PILOT_EXPORT_AWS_PROFILES"].get(pilot_id)
    session = boto3.Session(profile_name=export_profile) if export_profile else boto3.Session()
    s3 = session.client("s3")
    try:
        resp = s3.get_object(Bucket=SNAPSHOT_BUCKET, Key=key)
        data = json.loads(resp["Body"].read().decode("utf-8"))
        return set(data["users"])
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        logger.error("Failed to load snapshot for pilot %s on %s: %s", pilot_id, date_str, exc)
        return None


# ─── Date validation ─────────────────────────────────────────────────────────

def is_within_30_days(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = date.today()
        return today - timedelta(days=30) <= dt <= today
    except ValueError:
        return False


def parse_date_from_form(form_inputs):
    date_input = form_inputs.get("selected_date", {}).get("dateInput", {})
    ms = date_input.get("msSinceEpoch")
    if ms:
        try:
            dt = datetime.utcfromtimestamp(int(ms) / 1000)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    year = date_input.get("year")
    month = date_input.get("month")
    day = date_input.get("day")
    if year and month and day:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return None


def parse_hour_from_form(form_inputs):
    selection = form_inputs.get("selected_hour", {})
    items = selection.get("stringInputs", {}).get("value", [])
    if items:
        try:
            return int(items[0])
        except (ValueError, IndexError):
            pass
    return None


# ─── Background solar analysis ───────────────────────────────────────────────

def run_analysis_background(pilot_id, date_str, webhook_url, thread_name):
    cfg = load_pilot_configs()
    pilot_name = cfg["PILOT_NAMES"].get(pilot_id, str(pilot_id))
    try:
        expected_users = load_expected_users(pilot_id, date_str)
        if expected_users is None:
            _post_text(
                webhook_url, thread_name,
                f"⚠️ No DB snapshot found for *{pilot_name}* on *{date_str}*. "
                f"The daily snapshot job may not have run yet for that date.",
            )
            return

        read_profile = cfg["PILOT_AWS_PROFILES"].get(pilot_id)
        write_profile = cfg["PILOT_EXPORT_AWS_PROFILES"].get(pilot_id)
        aws_session = build_boto3_session(read_profile)
        export_session = build_boto3_session(write_profile)
        export_s3 = export_session.client("s3")

        variants = cfg["PILOT_REPORT_VARIANTS"].get(pilot_id) or [{"name": pilot_name}]

        for variant in variants:
            variant_name = variant.get("name") or pilot_name
            pilot_s3 = variant.get("s3") or cfg["PILOT_S3_PREFIXES"].get(pilot_id, {})
            bucket = pilot_s3.get("bucket", BUCKET)
            base_prefix = pilot_s3.get("base_prefix", BASE_PREFIX)
            export_s3_uri = variant.get("export_s3")

            s3_client = S3Client(date_str, date_str, bucket=bucket, session=aws_session)
            processor = DataProcessor(s3_client)
            report = ReportGenerator()
            chart_generator = HourlyChartGenerator()

            date_prefix = f"{base_prefix.rstrip('/')}/date={date_str}/"
            dt, daily_data, hourly_data = processor.process_date(date_prefix)

            if dt is None:
                _post_text(
                    webhook_url, thread_name,
                    f"⚠️ No S3 data found for *{variant_name}* on *{date_str}*.",
                )
                continue

            stats = compute_hourly_stats(expected_users, hourly_data)
            report.generate_hourly_report(dt, expected_users, hourly_data)
            summary_csv_path = report.generate_hourly_summary_csv(dt, stats["total_users"], hourly_data)
            chart_path, chart_content_type = chart_generator.generate_hourly_chart_image(
                dt, stats["total_users"], stats["present_counts"], stats["missing_counts"]
            )

            upload_hourly_user_files_to_s3(export_s3, dt, pilot=pilot_id, export_s3_uri=export_s3_uri)
            csv_s3_path, csv_url = upload_csv_to_s3_and_get_url(
                export_s3, dt, summary_csv_path, pilot=pilot_id, export_s3_uri=export_s3_uri
            )
            chart_url = upload_chart_to_s3_and_get_url(
                export_s3, dt, chart_path, chart_content_type, pilot=pilot_id, variant_name=variant_name
            )
            chart_preview_url = None
            if chart_url:
                sep = "&" if "?" in chart_url else "?"
                chart_preview_url = f"{chart_url}{sep}v={int(time.time())}"

            message_text = build_gchat_message(
                pilot_id=pilot_id,
                dt=dt,
                stats=stats,
                csv_s3_path=csv_s3_path,
                csv_presigned_url=csv_url,
                pilot_name=variant_name,
            )

            _post_report(webhook_url, thread_name, message_text, chart_preview_url, chart_url, csv_url)
            del daily_data

    except Exception as exc:
        logger.error("Analysis failed for pilot %s on %s: %s", pilot_id, date_str, exc)
        _post_text(
            webhook_url, thread_name,
            f"❌ Analysis failed for *{pilot_name}* on *{date_str}*: {exc}",
        )


# ─── Hourly check ─────────────────────────────────────────────────────────────

def get_hourly_check_text(pilot_ids, target_datetime_str=None):
    cfg = load_pilot_configs()
    dt_str, hour = resolve_target_window(target_datetime_str)
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    lines = [f"*⚡ Hourly Check — {dt_str} {hour:02d}:00 UTC  ({now_ist})*\n"]
    lines.append(f"{'Pilot':<12} {'Expected':>9} {'Present':>8} {'Missing':>8}  Status")
    lines.append("─" * 55)

    for pilot_id in pilot_ids:
        solar = check_solar_for_pilot(pilot_id, dt_str, hour)
        pilot_name = cfg["PILOT_NAMES"].get(pilot_id, str(pilot_id))
        if solar.get("status") != "ok":
            lines.append(f"{pilot_name:<12}  {'–':>9}  {'–':>8}  {'–':>8}  ❌ {solar.get('message', 'error')}")
            continue

        expected = solar["expected_count"]
        present = solar["present_count"]
        missing = solar["missing_count"]
        threshold = cfg["PILOT_HOURLY_TRIGGER_THRESHOLDS"].get(pilot_id, {"mode": "count", "value": 1})

        if threshold["mode"] == "percent":
            import math
            trigger = missing >= math.ceil(expected * threshold["value"])
        else:
            trigger = missing >= threshold["value"]

        status = "⚠️ Alert" if trigger else "✅ OK"
        lines.append(f"{pilot_name:<12}  {expected:>9,}  {present:>8,}  {missing:>8,}  {status}")

    return "\n".join(lines)


# ─── Pilot summary ────────────────────────────────────────────────────────────

def get_pilot_summary_text(pilot_ids):
    cfg = load_pilot_configs()
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"*📋 Pilot Summary — {today}*\n"]
    lines.append(f"{'Pilot':<12} {'Snapshot':>9}  Snapshot status")
    lines.append("─" * 45)

    for pilot_id in pilot_ids:
        pilot_name = cfg["PILOT_NAMES"].get(pilot_id, str(pilot_id))
        users = load_expected_users(pilot_id, today)
        if users is None:
            lines.append(f"{pilot_name:<12}  {'–':>9}  ⚠️ No snapshot for today")
        else:
            lines.append(f"{pilot_name:<12}  {len(users):>9,}  ✅ Snapshot ready")

    lines.append("\n_Run Solar Analysis to see hourly data for a specific date._")
    return "\n".join(lines)


# ─── Missing users helper ─────────────────────────────────────────────────────

def get_missing_users(pilot_id, date_str, hour):
    cfg = load_pilot_configs()
    expected_users = load_expected_users(pilot_id, date_str)
    if expected_users is None:
        return None, None

    read_profile = cfg["PILOT_AWS_PROFILES"].get(pilot_id)
    aws_session = build_boto3_session(read_profile)
    pilot_s3 = cfg["PILOT_S3_PREFIXES"].get(pilot_id, {})
    bucket = pilot_s3.get("bucket", BUCKET)
    base_prefix = pilot_s3.get("base_prefix", BASE_PREFIX)

    hour_prefix = f"{base_prefix.rstrip('/')}/date={date_str}/hour={hour:02d}/"
    s3_client = S3Client(date_str, date_str, bucket=bucket, session=aws_session)

    try:
        present_users = s3_client.read_hour_data(hour_prefix)
    except Exception as exc:
        logger.error("S3 read failed for pilot %s %s hour %s: %s", pilot_id, date_str, hour, exc)
        return expected_users, None

    missing = sorted(expected_users - present_users)
    return expected_users, missing


# ─── Outbound webhook helpers ─────────────────────────────────────────────────

def _thread_webhook_url(webhook_url, thread_name):
    if not webhook_url or not thread_name:
        return webhook_url
    thread_key = thread_name.replace("/", "_")
    sep = "&" if "?" in webhook_url else "?"
    return (
        f"{webhook_url}{sep}"
        f"threadKey={urllib.parse.quote(thread_key)}"
        f"&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    )


def _post_text(webhook_url, thread_name, text):
    if not webhook_url:
        logger.warning("No GCHAT_WEBHOOK_URL configured; skipping async post.")
        return
    url = _thread_webhook_url(webhook_url, thread_name)
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urlrequest.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=10):
            pass
    except Exception as exc:
        logger.error("Failed to post text to Google Chat: %s", exc)


def _post_report(webhook_url, thread_name, text, image_url, chart_url, csv_url):
    if not webhook_url:
        logger.warning("No GCHAT_WEBHOOK_URL configured; skipping async post.")
        return
    url = _thread_webhook_url(webhook_url, thread_name)

    sections = [{"widgets": [{"textParagraph": {"text": text}}]}]
    if image_url:
        image_widgets = [{"image": {"imageUrl": image_url, "altText": "Hourly chart"}}]
        btns = []
        if chart_url:
            btns.append({"textButton": {"text": "Download Chart", "onClick": {"openLink": {"url": chart_url}}}})
        if csv_url:
            btns.append({"textButton": {"text": "Download CSV", "onClick": {"openLink": {"url": csv_url}}}})
        if btns:
            image_widgets.append({"buttons": btns})
        sections.append({"widgets": image_widgets})

    payload = json.dumps({
        "cards": [{
            "header": {"title": "☀️ Solar Analysis Result"},
            "sections": sections,
        }]
    }).encode("utf-8")
    req = urlrequest.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=10):
            pass
    except Exception as exc:
        logger.error("Failed to post report to Google Chat: %s", exc)
        _post_text(webhook_url, thread_name, text)
