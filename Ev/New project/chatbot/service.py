from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import get_mysql_config, get_redshift_config, get_s3_stage_config, get_utils_repo_path, get_uat_mysql_config, get_uat_redshift_config
from app.pilots import get_pilot_definition
from app.redshift_services import run_redshift_analysis
from app.repo_utils import export_scripts_to_utils_repo
from app.s3_utils import build_archive_run_prefix
from app.script_generation import generate_db_update_scripts, generate_special_request_scripts
from app.services import export_mysql_meter_lists, export_special_request_meter_lists

from .gchat_webhook import post_run_summary_to_google_chat
from .schemas import NormalRunRequest, RunSummaryResponse, SpecialRunRequest
from .storage import save_run_summary


def _build_run_id(pilot: str, date_str: str) -> str:
    timestamp = datetime.now().strftime("%H%M%S")
    return f"{pilot}-{date_str}-{timestamp}"


def run_normal_flow(request: NormalRunRequest) -> RunSummaryResponse:
    pilot = get_pilot_definition(request.pilot)
    dated_output_dir = Path(request.output_dir) / request.date
    archive_timestamp = datetime.now().strftime("%H%M%S")

    mysql_config = get_uat_mysql_config() if pilot.uat else get_mysql_config()
    redshift_config = get_uat_redshift_config() if pilot.uat else get_redshift_config()
    s3_stage_config = get_s3_stage_config()
    archive_run_prefix = build_archive_run_prefix(
        base_prefix=s3_stage_config.prefix,
        pilot_key=pilot.key,
        date_str=request.date,
        timestamp=archive_timestamp,
    )

    export_summary = export_mysql_meter_lists(
        config=mysql_config,
        s3_stage_config=s3_stage_config,
        pilot_id=pilot.pilot_id,
        pilot_name=pilot.display_name,
        output_dir=dated_output_dir,
        checkforev_zero_min_id=request.checkforev_zero_min_id,
        archive_run_prefix=archive_run_prefix,
    )

    analysis_summary = run_redshift_analysis(
        config=redshift_config,
        s3_stage_config=s3_stage_config,
        pilot=pilot,
        output_dir=dated_output_dir,
        archive_run_prefix=archive_run_prefix,
    )

    script_summary = generate_db_update_scripts(
        pilot=pilot,
        output_dir=dated_output_dir,
    )

    branch_name: str | None = None
    pr_url: str | None = None
    if request.create_pr:
        repo_path = Path(request.repo_path) if request.repo_path else get_utils_repo_path(required=True)
        repo_summary = export_scripts_to_utils_repo(
            pilot_name=pilot.display_name,
            date_str=request.date,
            repo_path=repo_path,
            scripts_dir=script_summary.mark_completed_hsm_has.file_paths[0].parent,
            create_pr=True,
            push=False,
        )
        branch_name = repo_summary.branch_name
        pr_url = repo_summary.pr_url

    summary = RunSummaryResponse(
        run_id=_build_run_id(request.pilot, request.date),
        pilot=request.pilot,
        date=request.date,
        status="completed",
        output_dir=str(script_summary.mark_completed_hsm_has.file_paths[0].parent),
        frontend_url=request.frontend_url,
        branch_name=branch_name,
        pr_url=pr_url,
        mysql_counts={
            "full_list": export_summary.full_list.row_count,
            "request_sent": export_summary.request_sent_list.row_count,
            "checkforev_0": export_summary.checkforev_zero_list.row_count,
            "null_config": export_summary.null_config_count,
            "effective": export_summary.effective_list.row_count,
        },
        redshift_counts={
            "hsm_has_completed": analysis_summary.hsm_has_completed.row_count,
            "hsm_completed": analysis_summary.hsm_completed.row_count,
            "has_completed": analysis_summary.has_completed.row_count,
            "has_retry": analysis_summary.has_retry.row_count,
            "hsm_retry": analysis_summary.hsm_retry.row_count,
            "leftovers": analysis_summary.leftovers.row_count if analysis_summary.leftovers else 0,
        },
        script_counts={
            "mark_completed_hsm_has_files": len(script_summary.mark_completed_hsm_has.file_paths),
            "mark_completed_hsm_only_files": len(script_summary.mark_completed_hsm_only.file_paths),
            "mark_failed_files": len(script_summary.mark_failed_request_sent.file_paths),
            "retry_has_files": len(script_summary.retry_has_ev.file_paths),
            "retry_hsm_files": len(script_summary.retry_hsm_ev.file_paths),
        },
        mysql_meter_files={
            "full_list_s3_uri": export_summary.full_list.s3_uri,
            "request_sent_s3_uri": export_summary.request_sent_list.s3_uri,
            "checkforev_0_s3_uri": export_summary.checkforev_zero_list.s3_uri,
            "effective_s3_uri": export_summary.effective_list.s3_uri,
        },
        redshift_meter_files={
            "hsm_has_completed_s3_uri": analysis_summary.hsm_has_completed.s3_uri,
            "hsm_completed_s3_uri": analysis_summary.hsm_completed.s3_uri,
            "has_retry_s3_uri": analysis_summary.has_retry.s3_uri,
            "hsm_retry_s3_uri": analysis_summary.hsm_retry.s3_uri,
            "leftovers_s3_uri": analysis_summary.leftovers.s3_uri if analysis_summary.leftovers else None,
        },
        meter_files_folder_s3_uri=export_summary.archive_folder_s3_uri,
        message=f"{pilot.display_name} run completed for {request.date}",
    )
    save_run_summary(summary)
    if request.gchat_webhook_url:
        post_run_summary_to_google_chat(summary, request.gchat_webhook_url)
    return summary


def run_special_flow(request: SpecialRunRequest) -> RunSummaryResponse:
    pilot = get_pilot_definition(request.pilot)
    request_output_dir = Path(request.output_dir) / request.request_name
    archive_timestamp = datetime.now().strftime("%H%M%S")

    mysql_config = get_uat_mysql_config() if pilot.uat else get_mysql_config()
    redshift_config = get_uat_redshift_config() if pilot.uat else get_redshift_config()
    s3_stage_config = get_s3_stage_config()
    archive_run_prefix = build_archive_run_prefix(
        base_prefix=s3_stage_config.prefix,
        pilot_key=pilot.key,
        date_str=request.date,
        timestamp=archive_timestamp,
        request_name=request.request_name,
    )

    export_summary = export_special_request_meter_lists(
        config=mysql_config,
        s3_stage_config=s3_stage_config,
        pilot_id=pilot.pilot_id,
        pilot_name=pilot.display_name,
        output_dir=request_output_dir,
        checkforev_zero_min_id=request.checkforev_zero_min_id,
        meter_list_s3_uri=request.meter_list_s3,
        aws_region=s3_stage_config.region,
        archive_run_prefix=archive_run_prefix,
    )

    analysis_summary = run_redshift_analysis(
        config=redshift_config,
        s3_stage_config=s3_stage_config,
        pilot=pilot,
        output_dir=request_output_dir,
        archive_run_prefix=archive_run_prefix,
        table_name_prefix=f"{pilot.key}_{request.request_name}",
    )

    script_summary = generate_special_request_scripts(
        pilot=pilot,
        output_dir=request_output_dir,
    )

    branch_name: str | None = None
    pr_url: str | None = None
    if request.create_pr:
        repo_path = Path(request.repo_path) if request.repo_path else get_utils_repo_path(required=True)
        repo_summary = export_scripts_to_utils_repo(
            pilot_name=pilot.display_name,
            date_str=request.date,
            repo_path=repo_path,
            scripts_dir=script_summary.mark_completed_hsm_has.file_paths[0].parent,
            create_pr=True,
            push=False,
        )
        branch_name = repo_summary.branch_name
        pr_url = repo_summary.pr_url

    summary = RunSummaryResponse(
        run_id=_build_run_id(f"{request.pilot}-{request.request_name}", request.date),
        pilot=request.pilot,
        date=request.date,
        status="completed",
        output_dir=str(script_summary.mark_completed_hsm_has.file_paths[0].parent),
        frontend_url=request.frontend_url,
        branch_name=branch_name,
        pr_url=pr_url,
        mysql_counts={
            "full_list": export_summary.full_list.row_count,
            "request_sent": export_summary.request_sent_list.row_count,
            "checkforev_0": export_summary.checkforev_zero_list.row_count,
            "null_config": export_summary.null_config_count,
            "effective": export_summary.effective_list.row_count,
        },
        redshift_counts={
            "hsm_has_completed": analysis_summary.hsm_has_completed.row_count,
            "hsm_completed": analysis_summary.hsm_completed.row_count,
            "has_completed": analysis_summary.has_completed.row_count,
            "has_retry": analysis_summary.has_retry.row_count,
            "hsm_retry": analysis_summary.hsm_retry.row_count,
            "leftovers": analysis_summary.leftovers.row_count if analysis_summary.leftovers else 0,
        },
        script_counts={
            "mark_completed_hsm_has_files": len(script_summary.mark_completed_hsm_has.file_paths),
            "mark_completed_hsm_only_files": len(script_summary.mark_completed_hsm_only.file_paths),
            "mark_failed_files": len(script_summary.mark_failed_request_sent.file_paths),
            "retry_has_files": len(script_summary.retry_has_ev.file_paths),
            "retry_hsm_files": len(script_summary.retry_hsm_ev.file_paths),
        },
        mysql_meter_files={
            "full_list_s3_uri": export_summary.full_list.s3_uri,
            "request_sent_s3_uri": export_summary.request_sent_list.s3_uri,
            "checkforev_0_s3_uri": export_summary.checkforev_zero_list.s3_uri,
            "effective_s3_uri": export_summary.effective_list.s3_uri,
        },
        redshift_meter_files={
            "hsm_has_completed_s3_uri": analysis_summary.hsm_has_completed.s3_uri,
            "hsm_completed_s3_uri": analysis_summary.hsm_completed.s3_uri,
            "has_retry_s3_uri": analysis_summary.has_retry.s3_uri,
            "hsm_retry_s3_uri": analysis_summary.hsm_retry.s3_uri,
            "leftovers_s3_uri": analysis_summary.leftovers.s3_uri if analysis_summary.leftovers else None,
        },
        meter_files_folder_s3_uri=export_summary.archive_folder_s3_uri,
        message=f"{pilot.display_name} special request completed for {request.date}",
    )
    save_run_summary(summary)
    if request.gchat_webhook_url:
        post_run_summary_to_google_chat(summary, request.gchat_webhook_url)
    return summary
