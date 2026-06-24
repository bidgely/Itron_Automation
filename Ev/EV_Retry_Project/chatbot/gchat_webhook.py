from __future__ import annotations

import requests

from .schemas import RunSummaryResponse


def build_run_summary_text(summary: RunSummaryResponse) -> str:
    effective = summary.mysql_counts.get(
        "effective",
        summary.mysql_counts.get("full_list", 0) - summary.mysql_counts.get("checkforev_0", 0),
    )
    lines = [
        f"{summary.pilot.upper()} run completed for {summary.date}",
        "",
        "MySQL Counts",
        f"- Full list: {summary.mysql_counts.get('full_list', 0)}",
        f"- CheckForEV=0: {summary.mysql_counts.get('checkforev_0', 0)}",
        f"- Null configs: {summary.mysql_counts.get('null_config', 0)}",
        f"- Effective: {effective}",
        "",
        "Redshift Counts",
        f"- HSM+HAS completed: {summary.redshift_counts.get('hsm_has_completed', 0)}",
        f"- HSM completed: {summary.redshift_counts.get('hsm_completed', 0)}",
        f"- HAS completed: {summary.redshift_counts.get('has_completed', 0)}",
        f"- HAS retry: {summary.redshift_counts.get('has_retry', 0)}",
        f"- HSM retry: {summary.redshift_counts.get('hsm_retry', 0)}",
        "",
        "Scripts",
        f"- HSM+HAS complete files: {summary.script_counts.get('mark_completed_hsm_has_files', 0)}",
        f"- HSM only complete files: {summary.script_counts.get('mark_completed_hsm_only_files', 0)}",
        f"- Mark failed files: {summary.script_counts.get('mark_failed_files', 0)}",
        f"- HAS retry files: {summary.script_counts.get('retry_has_files', 0)}",
        f"- HSM retry files: {summary.script_counts.get('retry_hsm_files', 0)}",
        "",
        "Segregated Meters Extracted Folder",
        f"{summary.meter_files_folder_s3_uri or '-'}",
    ]

    if summary.branch_name:
        lines.extend(["", f"Branch: {summary.branch_name}"])
    if summary.pr_url:
        lines.append(f"PR: {summary.pr_url}")
    if summary.output_dir:
        lines.append(f"Output: {summary.output_dir}")
    if summary.frontend_url:
        lines.extend(["", f"For deeper analysis and operations: {summary.frontend_url}"])

    return "\n".join(lines)


def post_run_summary_to_google_chat(summary: RunSummaryResponse, webhook_url: str, thread_name: str | None = None) -> None:
    payload: dict = {"text": build_run_summary_text(summary)}
    url = webhook_url
    if thread_name:
        payload["thread"] = {"name": thread_name}
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    response = requests.post(
        url,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
