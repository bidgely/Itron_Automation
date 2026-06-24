from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .schemas import RunSectionResponse, RunSummaryResponse


STATE_ROOT = Path("chatbot_state/runs")
SESSION_ROOT = Path("chatbot_state/sessions")


def _run_path(run_id: str) -> Path:
    return STATE_ROOT / f"{run_id}.json"


def _session_path(session_id: str) -> Path:
    return SESSION_ROOT / f"{session_id}.json"


def cleanup_old_runs(days: int = 30) -> None:
    """Delete run summary files older than `days` days."""
    if not STATE_ROOT.exists():
        return
    cutoff = datetime.now() - timedelta(days=days)
    for path in STATE_ROOT.glob("*.json"):
        # filename format: {pilot}-{YYYYMMDD}-{HHMMSS}.json
        # Find the 8-digit date segment anywhere in the stem
        date_str = None
        for part in path.stem.split("-"):
            if len(part) == 8 and part.isdigit():
                date_str = part
                break
        if date_str:
            try:
                if datetime.strptime(date_str, "%Y%m%d") < cutoff:
                    path.unlink()
            except ValueError:
                pass


def save_run_summary(summary: RunSummaryResponse) -> Path:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_old_runs()
    output_path = _run_path(summary.run_id)
    output_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return output_path


def save_chat_session(session_id: str, payload: dict) -> Path:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = _session_path(session_id)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def load_chat_session(session_id: str) -> dict | None:
    input_path = _session_path(session_id)
    if not input_path.exists():
        return None
    return json.loads(input_path.read_text(encoding="utf-8"))


def clear_chat_session(session_id: str) -> None:
    input_path = _session_path(session_id)
    if input_path.exists():
        input_path.unlink()


def load_run_summary(run_id: str) -> RunSummaryResponse:
    input_path = _run_path(run_id)
    if not input_path.exists():
        raise FileNotFoundError(f"Run summary not found: {run_id}")
    data = json.loads(input_path.read_text(encoding="utf-8"))
    return RunSummaryResponse(**data)


def load_run_summary_by_date(pilot: str, date_str: str) -> RunSummaryResponse | None:
    """Load the most recent run summary for a pilot on a specific date."""
    if not STATE_ROOT.exists():
        return None
    matching_files = sorted(
        STATE_ROOT.glob(f"{pilot}-{date_str}-*.json"),
        key=lambda path: path.stem,
        reverse=True,
    )
    if not matching_files:
        return None
    data = json.loads(matching_files[0].read_text(encoding="utf-8"))
    return RunSummaryResponse(**data)


def load_latest_run_summary(pilot: str) -> RunSummaryResponse:
    if not STATE_ROOT.exists():
        raise FileNotFoundError(f"No runs found for pilot: {pilot}")

    matching_files = sorted(
        STATE_ROOT.glob(f"{pilot}-*.json"),
        key=lambda path: path.stem,
        reverse=True,
    )
    if not matching_files:
        raise FileNotFoundError(f"No runs found for pilot: {pilot}")

    data = json.loads(matching_files[0].read_text(encoding="utf-8"))
    return RunSummaryResponse(**data)


def build_run_section(run_id: str, section: str) -> RunSectionResponse:
    summary = load_run_summary(run_id)

    if section == "mysql":
        data = {**summary.mysql_counts, **summary.mysql_meter_files}
    elif section == "redshift":
        data = {**summary.redshift_counts, **summary.redshift_meter_files}
    elif section == "scripts":
        data = summary.script_counts
    elif section == "pr":
        data = {
            "branch_name": summary.branch_name,
            "pr_url": summary.pr_url,
            "status": summary.status,
        }
    else:
        raise ValueError(f"Unsupported section: {section}")

    return RunSectionResponse(
        run_id=summary.run_id,
        pilot=summary.pilot,
        date=summary.date,
        section=section,
        data=data,
    )
