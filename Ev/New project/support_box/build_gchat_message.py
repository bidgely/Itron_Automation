from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _extract_count(output: str, label: str) -> int | None:
    pattern = re.compile(rf"{re.escape(label)}(?:\s*:\s*|\s*->\s*)(\d+)")
    match = pattern.search(output)
    return int(match.group(1)) if match else None


def _extract_value(output: str, label: str) -> str | None:
    pattern = re.compile(rf"{re.escape(label)}(?:\s*:\s*|\s*->\s*)(.+)")
    match = pattern.search(output)
    return match.group(1).strip() if match else None


def _script_counts_from_dir(scripts_dir: Path) -> dict[str, int]:
    return {
        "hsm_has": len(list(scripts_dir.glob("*mark_completed_hsm_has.sh"))),
        "hsm_only": len(list(scripts_dir.glob("*mark_completed_hsm_only.sh"))),
        "mark_failed": len(list(scripts_dir.glob("*mark_failed_request_sent.sh"))),
        "has_retry": len(list(scripts_dir.glob("*retry_has_ev_config_generated.sh"))),
        "hsm_retry": len(list(scripts_dir.glob("*retry_hsm_ev_config_generated.sh"))),
    }


def build_daily_success_message(pilot: str, date_str: str, output: str) -> str:
    full_list = _extract_count(output, "full list") or 0
    checkforev_zero = _extract_count(output, "CheckForEV=0 list") or 0
    effective = full_list - checkforev_zero
    meter_files_folder = _extract_value(output, "meter files folder S3") or "-"

    hsm_has_completed = _extract_count(output, "HSM+HAS completed") or 0
    hsm_completed = _extract_count(output, "HSM completed") or 0
    has_retry = _extract_count(output, "HAS retry") or 0
    hsm_retry = _extract_count(output, "HSM retry") or 0
    leftovers = _extract_count(output, "leftovers") or 0

    scripts_dir_value = _extract_value(output, "scripts generated")
    script_counts = {
        "hsm_has": 0,
        "hsm_only": 0,
        "mark_failed": 0,
        "has_retry": 0,
        "hsm_retry": 0,
    }
    if scripts_dir_value:
        script_counts = _script_counts_from_dir(Path(scripts_dir_value))

    pr_url = _extract_value(output, "PR link") or ""

    lines = [
        f"{pilot.upper()} run completed for {date_str}",
        "",
        "MySQL Counts",
        f"Full EV list -> {full_list}",
        f"CheckForEV=0 -> {checkforev_zero}",
        f"Effective -> {effective}",
        "",
        "Redshift Counts",
        f"HSM+HAS completed -> {hsm_has_completed}",
        f"HSM completed -> {hsm_completed}",
        f"HAS retry -> {has_retry}",
        f"HSM retry -> {hsm_retry}",
        f"Leftovers -> {leftovers}",
        "",
        "Scripts",
        f"HSM+HAS complete files -> {script_counts['hsm_has']}",
        f"HSM only completed files -> {script_counts['hsm_only']}",
        f"Mark failed files -> {script_counts['mark_failed']}",
        f"HAS retry files -> {script_counts['has_retry']}",
        f"HSM retry files -> {script_counts['hsm_retry']}",
        "",
        "Segregated Meters Extracted Folder",
        f"{meter_files_folder}",
        "",
        f"PR -> {pr_url or '-'}",
    ]
    return "\n".join(lines)


def build_special_success_message(
    pilot: str,
    date_str: str,
    request_name: str,
    meter_list_s3: str,
    output: str,
) -> str:
    full_list = _extract_count(output, "special full list") or _extract_count(output, "full list") or 0
    checkforev_zero = _extract_count(output, "special CheckForEV=0 list") or _extract_count(output, "CheckForEV=0 list") or 0
    effective = full_list - checkforev_zero
    meter_files_folder = _extract_value(output, "meter files folder S3") or "-"
    hsm_has_completed = _extract_count(output, "HSM+HAS completed") or 0
    hsm_completed = _extract_count(output, "HSM completed") or 0
    has_retry = _extract_count(output, "HAS retry") or 0
    hsm_retry = _extract_count(output, "HSM retry") or 0
    pr_url = _extract_value(output, "PR link") or ""

    lines = [
        f"{pilot.upper()} special request completed for {date_str}",
        f"Request -> {request_name}",
        f"S3 file -> {meter_list_s3}",
        "",
        "MySQL Counts",
        f"Full EV list -> {full_list}",
        f"CheckForEV=0 -> {checkforev_zero}",
        f"Effective -> {effective}",
        "",
        "Redshift Counts",
        f"HSM+HAS completed -> {hsm_has_completed}",
        f"HSM completed -> {hsm_completed}",
        f"HAS retry -> {has_retry}",
        f"HSM retry -> {hsm_retry}",
        "",
        "Segregated Meters Extracted Folder",
        f"{meter_files_folder}",
        "",
        f"PR -> {pr_url or '-'}",
    ]
    return "\n".join(lines)


def build_failure_message(
    pilot: str,
    date_str: str,
    output: str,
    *,
    request_name: str | None = None,
    meter_list_s3: str | None = None,
    run_log: str | None = None,
) -> str:
    lines = [
        f"{pilot.upper()} {'special request ' if request_name else ''}failed for {date_str}",
    ]
    if request_name:
        lines.append(f"Request -> {request_name}")
    if meter_list_s3:
        lines.append(f"S3 file -> {meter_list_s3}")
    lines.extend(
        [
            "",
            "Error",
            output.strip()[-3000:],
        ]
    )
    if run_log:
        lines.extend(["", f"Log -> {run_log}"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact Google Chat message from automation output.")
    parser.add_argument("--pilot", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--status", required=True, choices=["success", "failure"])
    parser.add_argument("--mode", required=True, choices=["daily", "special"])
    parser.add_argument("--request-name")
    parser.add_argument("--meter-list-s3")
    parser.add_argument("--run-log")
    args = parser.parse_args()

    output = sys.stdin.read()

    if args.status == "success" and args.mode == "daily":
        message = build_daily_success_message(args.pilot, args.date, output)
    elif args.status == "success" and args.mode == "special":
        message = build_special_success_message(
            args.pilot,
            args.date,
            args.request_name or "",
            args.meter_list_s3 or "",
            output,
        )
    else:
        message = build_failure_message(
            args.pilot,
            args.date,
            output,
            request_name=args.request_name,
            meter_list_s3=args.meter_list_s3,
            run_log=args.run_log,
        )

    sys.stdout.write(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
