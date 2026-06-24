from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .config import get_mysql_config, get_redshift_config, get_s3_stage_config, get_utils_repo_path
from .pilots import get_pilot_definition, get_supported_pilot_keys
from .s3_utils import build_archive_run_prefix


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Itron automation helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export-mysql-lists",
        help="Export pilot meter lists from MySQL into CSV files.",
    )
    export_parser.add_argument(
        "--pilot",
        required=True,
        help="Configured pilot key, or 'all' to export every configured pilot.",
    )
    export_parser.add_argument(
        "--pilot-id",
        type=int,
        help="Optional pilot id override when you want to replace the preset value.",
    )
    export_parser.add_argument(
        "--output-dir",
        default="output",
        help="Base directory for generated CSV files.",
    )
    export_parser.add_argument(
        "--checkforev-zero-min-id",
        type=int,
        default=995,
        help="Lower id bound used for the CheckForEV=0 extraction query.",
    )

    analysis_parser = subparsers.add_parser(
        "run-redshift-analysis",
        help="Load exported CSVs into Redshift staging tables and generate pilot buckets.",
    )
    analysis_parser.add_argument(
        "--pilot",
        required=True,
        help="Configured pilot key, or 'all' to analyze every configured pilot.",
    )
    analysis_parser.add_argument(
        "--output-dir",
        default="output",
        help="Base directory containing exported CSV files.",
    )

    scripts_parser = subparsers.add_parser(
        "generate-db-update-scripts",
        help="Generate MySQL update shell scripts from analysis CSV files.",
    )
    scripts_parser.add_argument(
        "--pilot",
        required=True,
        help="Configured pilot key, or 'all' to generate scripts for every configured pilot.",
    )
    scripts_parser.add_argument(
        "--output-dir",
        default="output",
        help="Base directory containing analysis CSV files.",
    )

    pipeline_parser = subparsers.add_parser(
        "run-pilot-flow",
        help="Run MySQL export, Redshift analysis, and DB script generation in one go.",
    )
    pipeline_parser.add_argument(
        "--pilot",
        required=True,
        help="Configured pilot key, or 'all' to run the full flow for every configured pilot.",
    )
    pipeline_parser.add_argument(
        "--pilot-id",
        type=int,
        help="Optional pilot id override when running a single pilot.",
    )
    pipeline_parser.add_argument(
        "--output-dir",
        default="output",
        help="Base directory for generated files.",
    )
    pipeline_parser.add_argument(
        "--checkforev-zero-min-id",
        type=int,
        default=995,
        help="Lower id bound used for the CheckForEV=0 extraction query.",
    )

    special_parser = subparsers.add_parser(
        "run-special-request",
        help="Run a separate special-request flow using a client-provided S3 meter list.",
    )
    special_parser.add_argument(
        "--pilot",
        required=True,
        help="Configured pilot key to process for the special request.",
    )
    special_parser.add_argument(
        "--request-name",
        required=True,
        help="Short name used to isolate outputs and Redshift staging tables.",
    )
    special_parser.add_argument(
        "--meter-list-s3",
        required=True,
        help="S3 URI to the client-provided meter list CSV.",
    )
    special_parser.add_argument(
        "--output-dir",
        default="output/special",
        help="Base directory for special-request outputs.",
    )
    special_parser.add_argument(
        "--checkforev-zero-min-id",
        type=int,
        default=995,
        help="Lower id bound used for the CheckForEV=0 extraction query.",
    )

    repo_parser = subparsers.add_parser(
        "export-to-utils-repo",
        help="Copy generated scripts into the Utils repo, create the branch, and optionally create a PR.",
    )
    repo_parser.add_argument(
        "--pilot",
        required=True,
        help="Configured pilot whose generated scripts should be exported.",
    )
    repo_parser.add_argument(
        "--date",
        required=True,
        help="Target folder date in YYYYMMDD format.",
    )
    repo_parser.add_argument(
        "--repo-path",
        help="Local path to the Utils repo. If omitted, uses ITRON_UTILS_REPO_PATH or UTILS_REPO_PATH.",
    )
    repo_parser.add_argument(
        "--scripts-dir",
        help="Optional explicit scripts directory. Defaults to output/<pilot>/scripts.",
    )
    repo_parser.add_argument(
        "--create-pr",
        action="store_true",
        help="Create a GitHub PR after pushing the branch.",
    )
    repo_parser.add_argument(
        "--push",
        action="store_true",
        help="Push the branch even if --create-pr is not used.",
    )

    full_repo_flow_parser = subparsers.add_parser(
        "run-pilot-flow-and-create-pr",
        help="Run the full pilot flow, then export generated scripts to Utils and create a PR.",
    )
    full_repo_flow_parser.add_argument(
        "--pilot",
        required=True,
        help="Configured pilot to process.",
    )
    full_repo_flow_parser.add_argument(
        "--date",
        required=True,
        help="Target repo folder date in YYYYMMDD format.",
    )
    full_repo_flow_parser.add_argument(
        "--repo-path",
        help="Local path to the Utils repo. If omitted, uses ITRON_UTILS_REPO_PATH or UTILS_REPO_PATH.",
    )
    full_repo_flow_parser.add_argument(
        "--output-dir",
        default="output",
        help="Base directory for generated files.",
    )
    full_repo_flow_parser.add_argument(
        "--checkforev-zero-min-id",
        type=int,
        default=995,
        help="Lower id bound used for the CheckForEV=0 extraction query.",
    )
    full_repo_flow_parser.add_argument(
        "--create-pr",
        action="store_true",
        help="Create a GitHub PR after pushing the branch.",
    )
    full_repo_flow_parser.add_argument(
        "--push",
        action="store_true",
        help="Push the branch even if --create-pr is not used.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    def resolve_repo_path(repo_path_value: str | None, *, required: bool) -> Path:
        if repo_path_value:
            return Path(repo_path_value)
        resolved = get_utils_repo_path(required=required)
        assert resolved is not None
        return resolved

    def script_summary_line(label: str, generated_script) -> str:
        script_dir = generated_script.file_paths[0].parent if generated_script.file_paths else Path(".")
        return (
            f"{label}: {generated_script.statement_count} statements "
            f"across {len(generated_script.file_paths)} file(s) -> {script_dir}"
        )

    def create_archive_run_prefix(
        *,
        pilot_key: str,
        date_str: str,
        request_name: str | None = None,
    ) -> str:
        return build_archive_run_prefix(
            base_prefix=get_s3_stage_config().prefix,
            pilot_key=pilot_key,
            date_str=date_str,
            timestamp=datetime.now().strftime("%H%M%S"),
            request_name=request_name,
        )

    if args.command == "export-mysql-lists":
        from .services import export_mysql_meter_lists

        config = get_mysql_config()
        s3_stage_config = get_s3_stage_config()
        pilot_keys = get_supported_pilot_keys() if args.pilot == "all" else [args.pilot]

        for pilot_key in pilot_keys:
            pilot = get_pilot_definition(pilot_key)
            pilot_id = args.pilot_id if args.pilot != "all" and args.pilot_id else pilot.pilot_id
            summary = export_mysql_meter_lists(
                config=config,
                s3_stage_config=s3_stage_config,
                pilot_id=pilot_id,
                pilot_name=pilot.display_name,
                output_dir=Path(args.output_dir),
                checkforev_zero_min_id=args.checkforev_zero_min_id,
            )
            print(f"[{pilot.display_name}] full list: {summary.full_list.row_count} -> {summary.full_list.file_path}")
            print(
                f"[{pilot.display_name}] request-sent list: "
                f"{summary.request_sent_list.row_count} -> {summary.request_sent_list.file_path}"
            )
            print(
                f"[{pilot.display_name}] CheckForEV=0 list: "
                f"{summary.checkforev_zero_list.row_count} -> {summary.checkforev_zero_list.file_path}"
            )
            print(
                f"[{pilot.display_name}] effective list: "
                f"{summary.effective_list.row_count} -> {summary.effective_list.file_path}"
            )
            print(
                f"[{pilot.display_name}] latest HSM_EV config rows: "
                f"{summary.latest_hsm_config.row_count} -> {summary.latest_hsm_config.file_path}"
            )
            print(f"[{pilot.display_name}] meter files folder S3: {summary.archive_folder_s3_uri}")
        return 0

    if args.command == "run-redshift-analysis":
        from .redshift_services import run_redshift_analysis

        config = get_redshift_config()
        s3_stage_config = get_s3_stage_config()
        pilot_keys = get_supported_pilot_keys() if args.pilot == "all" else [args.pilot]

        for pilot_key in pilot_keys:
            pilot = get_pilot_definition(pilot_key)
            summary = run_redshift_analysis(
                config=config,
                s3_stage_config=s3_stage_config,
                pilot=pilot,
                output_dir=Path(args.output_dir),
            )
            print(
                f"[{pilot.display_name}] HSM+HAS completed: "
                f"{summary.hsm_has_completed.row_count} -> {summary.hsm_has_completed.file_path}"
            )
            print(
                f"[{pilot.display_name}] HSM completed: "
                f"{summary.hsm_completed.row_count} -> {summary.hsm_completed.file_path}"
            )
            print(
                f"[{pilot.display_name}] HAS retry: "
                f"{summary.has_retry.row_count} -> {summary.has_retry.file_path}"
            )
            print(
                f"[{pilot.display_name}] HSM retry: "
                f"{summary.hsm_retry.row_count} -> {summary.hsm_retry.file_path}"
            )
            if summary.leftovers is not None:
                print(
                    f"[{pilot.display_name}] leftovers: "
                    f"{summary.leftovers.row_count} -> {summary.leftovers.file_path}"
                )
            print(f"[{pilot.display_name}] meter files folder S3: {summary.archive_folder_s3_uri}")
        return 0

    if args.command == "generate-db-update-scripts":
        from .script_generation import generate_db_update_scripts

        pilot_keys = get_supported_pilot_keys() if args.pilot == "all" else [args.pilot]
        for pilot_key in pilot_keys:
            pilot = get_pilot_definition(pilot_key)
            summary = generate_db_update_scripts(
                pilot=pilot,
                output_dir=Path(args.output_dir),
            )
            print(f"[{pilot.display_name}] {script_summary_line('mark completed HSM+HAS', summary.mark_completed_hsm_has)}")
            print(f"[{pilot.display_name}] {script_summary_line('mark completed HSM only', summary.mark_completed_hsm_only)}")
            print(f"[{pilot.display_name}] {script_summary_line('mark failed request-sent', summary.mark_failed_request_sent)}")
            print(f"[{pilot.display_name}] {script_summary_line('retry HAS_EV', summary.retry_has_ev)}")
            print(f"[{pilot.display_name}] {script_summary_line('retry HSM_EV', summary.retry_hsm_ev)}")
        return 0

    if args.command == "run-pilot-flow":
        from .redshift_services import run_redshift_analysis
        from .script_generation import generate_db_update_scripts
        from .services import export_mysql_meter_lists

        s3_stage_config = get_s3_stage_config()
        pilot_keys = get_supported_pilot_keys() if args.pilot == "all" else [args.pilot]

        for pilot_key in pilot_keys:
            pilot = get_pilot_definition(pilot_key)
            from .config import get_mysql_config_for_pilot, get_uat_redshift_config
            mysql_config = get_mysql_config_for_pilot(pilot.uat)
            redshift_config = get_uat_redshift_config() if pilot.uat else get_redshift_config()
            pilot_id = args.pilot_id if args.pilot != "all" and args.pilot_id else pilot.pilot_id
            archive_date = datetime.now().strftime("%Y%m%d")
            archive_run_prefix = build_archive_run_prefix(
                base_prefix=s3_stage_config.prefix,
                pilot_key=pilot.key,
                date_str=archive_date,
                timestamp=datetime.now().strftime("%H%M%S"),
            )

            export_summary = export_mysql_meter_lists(
                config=mysql_config,
                s3_stage_config=s3_stage_config,
                pilot_id=pilot_id,
                pilot_name=pilot.display_name,
                output_dir=Path(args.output_dir),
                checkforev_zero_min_id=args.checkforev_zero_min_id,
                archive_run_prefix=archive_run_prefix,
            )
            print(f"[{pilot.display_name}] full list: {export_summary.full_list.row_count}")
            print(f"[{pilot.display_name}] request-sent list: {export_summary.request_sent_list.row_count}")
            print(
                f"[{pilot.display_name}] CheckForEV=0 list: "
                f"{export_summary.checkforev_zero_list.row_count}"
            )
            print(f"[{pilot.display_name}] effective list: {export_summary.effective_list.row_count}")
            print(f"[{pilot.display_name}] latest HSM_EV config rows: {export_summary.latest_hsm_config.row_count}")

            analysis_summary = run_redshift_analysis(
                config=redshift_config,
                s3_stage_config=s3_stage_config,
                pilot=pilot,
                output_dir=Path(args.output_dir),
                archive_run_prefix=archive_run_prefix,
            )
            print(
                f"[{pilot.display_name}] HSM+HAS completed: "
                f"{analysis_summary.hsm_has_completed.row_count}"
            )
            print(
                f"[{pilot.display_name}] HSM completed: "
                f"{analysis_summary.hsm_completed.row_count}"
            )
            print(f"[{pilot.display_name}] HAS retry: {analysis_summary.has_retry.row_count}")
            print(f"[{pilot.display_name}] HSM retry: {analysis_summary.hsm_retry.row_count}")

            script_summary = generate_db_update_scripts(
                pilot=pilot,
                output_dir=Path(args.output_dir),
            )
            print(
                f"[{pilot.display_name}] scripts generated: "
                f"{script_summary.mark_completed_hsm_has.file_paths[0].parent}"
            )
            print(f"[{pilot.display_name}] meter files folder S3: {export_summary.archive_folder_s3_uri}")
        return 0

    if args.command == "run-special-request":
        from .redshift_services import run_redshift_analysis
        from .script_generation import generate_special_request_scripts
        from .services import export_special_request_meter_lists

        mysql_config = get_mysql_config()
        redshift_config = get_redshift_config()
        s3_stage_config = get_s3_stage_config()
        pilot = get_pilot_definition(args.pilot)
        request_output_dir = Path(args.output_dir) / args.request_name
        archive_date = datetime.now().strftime("%Y%m%d")
        archive_run_prefix = build_archive_run_prefix(
            base_prefix=s3_stage_config.prefix,
            pilot_key=pilot.key,
            date_str=archive_date,
            timestamp=datetime.now().strftime("%H%M%S"),
            request_name=args.request_name,
        )

        export_summary = export_special_request_meter_lists(
            config=mysql_config,
            s3_stage_config=s3_stage_config,
            pilot_id=pilot.pilot_id,
            pilot_name=pilot.display_name,
            output_dir=request_output_dir,
            checkforev_zero_min_id=args.checkforev_zero_min_id,
            meter_list_s3_uri=args.meter_list_s3,
            aws_region=s3_stage_config.region,
            archive_run_prefix=archive_run_prefix,
        )
        print(f"[{pilot.display_name}] special full list: {export_summary.full_list.row_count}")
        print(f"[{pilot.display_name}] special CheckForEV=0 list: {export_summary.checkforev_zero_list.row_count}")
        print(f"[{pilot.display_name}] special effective list: {export_summary.effective_list.row_count}")
        print(f"[{pilot.display_name}] special latest HSM_EV config rows: {export_summary.latest_hsm_config.row_count}")

        analysis_summary = run_redshift_analysis(
            config=redshift_config,
            s3_stage_config=s3_stage_config,
            pilot=pilot,
            output_dir=request_output_dir,
            archive_run_prefix=archive_run_prefix,
            table_name_prefix=f"{pilot.key}_{args.request_name}",
        )
        print(f"[{pilot.display_name}] HSM+HAS completed: {analysis_summary.hsm_has_completed.row_count}")
        print(f"[{pilot.display_name}] HSM completed: {analysis_summary.hsm_completed.row_count}")
        print(f"[{pilot.display_name}] HAS retry: {analysis_summary.has_retry.row_count}")
        print(f"[{pilot.display_name}] HSM retry: {analysis_summary.hsm_retry.row_count}")

        script_summary = generate_special_request_scripts(
            pilot=pilot,
            output_dir=request_output_dir,
        )
        print(
            f"[{pilot.display_name}] special-request scripts generated: "
            f"{script_summary.mark_completed_hsm_has.file_paths[0].parent}"
        )
        print(f"[{pilot.display_name}] meter files folder S3: {export_summary.archive_folder_s3_uri}")
        return 0

    if args.command == "export-to-utils-repo":
        from .repo_utils import export_scripts_to_utils_repo

        pilot = get_pilot_definition(args.pilot)
        scripts_dir = (
            Path(args.scripts_dir)
            if args.scripts_dir
            else Path("output") / pilot.key / "scripts"
        )
        summary = export_scripts_to_utils_repo(
            pilot_name=pilot.display_name,
            date_str=args.date,
            repo_path=resolve_repo_path(args.repo_path, required=True),
            scripts_dir=scripts_dir,
            create_pr=args.create_pr,
            push=args.push,
        )
        print(f"[{pilot.display_name}] branch: {summary.branch_name}")
        print(f"[{pilot.display_name}] copied files: {len(summary.copied_files)} -> {summary.destination_dir}")
        print(f"[{pilot.display_name}] commit created: {summary.commit_created}")
        print(f"[{pilot.display_name}] PR created: {summary.pr_created}")
        return 0

    if args.command == "run-pilot-flow-and-create-pr":
        from .redshift_services import run_redshift_analysis
        from .repo_utils import export_scripts_to_utils_repo
        from .script_generation import generate_db_update_scripts
        from .services import export_mysql_meter_lists
        from chatbot.schemas import RunSummaryResponse
        from chatbot.storage import save_run_summary

        s3_stage_config = get_s3_stage_config()
        pilot = get_pilot_definition(args.pilot)
        from .config import get_mysql_config_for_pilot, get_uat_redshift_config
        mysql_config = get_mysql_config_for_pilot(pilot.uat)
        redshift_config = get_uat_redshift_config() if pilot.uat else get_redshift_config()
        dated_output_dir = Path(args.output_dir) / args.date
        archive_run_prefix = build_archive_run_prefix(
            base_prefix=s3_stage_config.prefix,
            pilot_key=pilot.key,
            date_str=args.date,
            timestamp=datetime.now().strftime("%H%M%S"),
        )

        export_summary = export_mysql_meter_lists(
            config=mysql_config,
            s3_stage_config=s3_stage_config,
            pilot_id=pilot.pilot_id,
            pilot_name=pilot.display_name,
            output_dir=dated_output_dir,
            checkforev_zero_min_id=args.checkforev_zero_min_id,
            archive_run_prefix=archive_run_prefix,
        )
        print(f"[{pilot.display_name}] dated output directory: {dated_output_dir}")
        print(f"[{pilot.display_name}] full list: {export_summary.full_list.row_count}")
        print(f"[{pilot.display_name}] request-sent list: {export_summary.request_sent_list.row_count}")
        print(f"[{pilot.display_name}] CheckForEV=0 list: {export_summary.checkforev_zero_list.row_count}")
        print(f"[{pilot.display_name}] effective list: {export_summary.effective_list.row_count}")
        print(f"[{pilot.display_name}] latest HSM_EV config rows: {export_summary.latest_hsm_config.row_count}")

        analysis_summary = run_redshift_analysis(
            config=redshift_config,
            s3_stage_config=s3_stage_config,
            pilot=pilot,
            output_dir=dated_output_dir,
            archive_run_prefix=archive_run_prefix,
        )
        print(f"[{pilot.display_name}] HSM+HAS completed: {analysis_summary.hsm_has_completed.row_count}")
        print(f"[{pilot.display_name}] HSM completed: {analysis_summary.hsm_completed.row_count}")
        print(f"[{pilot.display_name}] HAS retry: {analysis_summary.has_retry.row_count}")
        print(f"[{pilot.display_name}] HSM retry: {analysis_summary.hsm_retry.row_count}")

        script_summary = generate_db_update_scripts(
            pilot=pilot,
            output_dir=dated_output_dir,
        )
        scripts_dir = script_summary.mark_completed_hsm_has.file_paths[0].parent
        print(f"[{pilot.display_name}] scripts generated: {scripts_dir}")
        print(f"[{pilot.display_name}] meter files folder S3: {export_summary.archive_folder_s3_uri}")

        repo_summary = export_scripts_to_utils_repo(
            pilot_name=pilot.display_name,
            date_str=args.date,
            repo_path=resolve_repo_path(args.repo_path, required=True),
            scripts_dir=scripts_dir,
            create_pr=args.create_pr,
            push=args.push,
        )
        print(f"[{pilot.display_name}] branch: {repo_summary.branch_name}")
        print(f"[{pilot.display_name}] copied files: {len(repo_summary.copied_files)} -> {repo_summary.destination_dir}")
        print(f"[{pilot.display_name}] commit created: {repo_summary.commit_created}")
        print(f"[{pilot.display_name}] PR created: {repo_summary.pr_created}")
        if repo_summary.pr_url:
            print(f"[{pilot.display_name}] PR link: {repo_summary.pr_url}")

        timestamp = datetime.now().strftime("%H%M%S")
        run_summary = RunSummaryResponse(
            run_id=f"{pilot.key}-{args.date}-{timestamp}",
            pilot=pilot.key,
            date=args.date,
            status="completed",
            output_dir=str(scripts_dir),
            branch_name=repo_summary.branch_name,
            pr_url=repo_summary.pr_url,
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
            message=f"{pilot.display_name} cron run completed for {args.date}",
        )
        save_run_summary(run_summary)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 1
