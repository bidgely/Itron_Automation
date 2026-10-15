from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from .db import redshift_connection
from .exporters import read_single_column_csv, write_single_column_csv
from .pilots import PilotDefinition
from .redshift_queries import (
    has_completed_sql,
    has_retry_sql,
    hsm_completed_sql,
    hsm_has_completed_sql,
    hsm_retry_sql,
    leftovers_sql,
    pilot_user_count_sql,
)
from .s3_utils import build_archive_run_prefix, delete_s3_prefix_older_than_days


@dataclass(frozen=True)
class AnalysisResult:
    file_path: Path
    row_count: int
    s3_uri: str | None = None


@dataclass(frozen=True)
class PilotAnalysisSummary:
    hsm_has_completed: AnalysisResult
    hsm_completed: AnalysisResult
    has_completed: AnalysisResult
    has_retry: AnalysisResult
    hsm_retry: AnalysisResult
    leftovers: AnalysisResult | None
    archive_folder_s3_uri: str
    total_user_count: int


def _sanitize_table_fragment(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")
    if not sanitized:
        raise ValueError("Request name produced an empty Redshift table fragment.")
    return sanitized[:48]


def _load_meter_csv(input_path: Path) -> list[str]:
    meter_ids = read_single_column_csv(input_path)
    if not meter_ids:
        raise ValueError(f"No meter ids found in {input_path}")
    return meter_ids


def _create_external_csv_table(cursor, table_name: str, columns_sql: str, s3_folder_uri: str) -> None:
    """Point a test_db table at a folder containing exactly one CSV file.

    Starburst has no equivalent of Redshift's COPY-from-a-single-file; instead a Hive-type
    external table reads every file under a folder, so each staged CSV must live in its own
    dedicated subfolder, one per table.
    """
    cursor.execute(f"DROP TABLE IF EXISTS test_db.{table_name}")
    cursor.execute(
        f"CREATE TABLE test_db.{table_name} ({columns_sql}) "
        "WITH (type = 'HIVE', format = 'CSV', "
        f"external_location = '{s3_folder_uri}', skip_header_line_count = 1)"
    )
    cursor.execute(f"GRANT ALL PRIVILEGES ON test_db.{table_name} TO ROLE public")


def _fetch_meter_ids(cursor, sql: str) -> list[str]:
    cursor.execute(sql)
    rows = cursor.fetchall()
    return [row[0] for row in rows if row and row[0]]


def _fetch_scalar_count(cursor, sql: str) -> int:
    cursor.execute(sql)
    rows = cursor.fetchall()
    return int(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else 0


def _write_result(output_dir: Path, filename: str, values: list[str]) -> AnalysisResult:
    output_path = output_dir / filename
    write_single_column_csv(output_path, "meterid", values)
    return AnalysisResult(output_path, len(values))


def _upload_analysis_result(local_path: Path, s3_stage_config, archive_run_prefix: str) -> str:
    from .s3_utils import upload_s3_file

    key = f"{archive_run_prefix}/{local_path.name}"
    return upload_s3_file(local_path, s3_stage_config.bucket, key, s3_stage_config.region)


def _stage_tables_for_pilot(
    connection,
    pilot: PilotDefinition,
    output_dir: Path,
    table_stage_s3_config,
    archive_run_prefix: str,
    table_name_prefix: str | None = None,
) -> tuple[str, str]:
    # table_stage_s3_config must be the bucket matching this pilot's own environment
    # (get_s3_stage_config_for_pilot) — Starburst cannot read across the uat/prod bucket
    # boundary when creating an external table. This is the ONLY place that distinction
    # matters; every other S3 use in this module (archiving results, cleanup) is plain
    # storage that Starburst never reads back, so it stays on the one shared bucket.
    prefix = pilot.display_name.title()
    if table_name_prefix:
        fragment = _sanitize_table_fragment(table_name_prefix)
        full_table = f"{fragment}_full_list"
        checkforev_zero_table = f"{fragment}_checkforev0"
    else:
        full_table = pilot.redshift_full_table or f"{prefix}_full_list_metersIds"
        checkforev_zero_table = pilot.redshift_checkforev_zero_table or f"{prefix}_Metersids_checkforev0_mysql"

    full_csv = output_dir / pilot.key / f"{pilot.key}_full_list_meters.csv"
    checkforev_zero_csv = output_dir / pilot.key / f"{pilot.key}_checkforev0_meters.csv"
    latest_hsm_config_csv = output_dir / pilot.key / f"{pilot.key}_latest_hsm_ev_config.csv"

    full_meter_ids = _load_meter_csv(full_csv)
    checkforev_zero_meter_ids = read_single_column_csv(checkforev_zero_csv)
    latest_hsm_config_count = max(sum(1 for _ in latest_hsm_config_csv.open("r", encoding="utf-8")) - 1, 0)
    latest_hsm_config_table = f"{full_table}_latest_hsm_ev_cfg"

    print(f"[{pilot.display_name}] Step 1/4: uploading CSV files to S3, one folder per table...")
    from .s3_utils import upload_s3_file

    # Starburst's Hive-type external tables read every file under a folder (there is no
    # Redshift-style COPY-from-a-single-file), so each CSV gets its own dedicated subfolder
    # named after the table it backs, keeping exactly one file per folder.
    full_folder = f"{archive_run_prefix}/{full_table}"
    checkforev_zero_folder = f"{archive_run_prefix}/{checkforev_zero_table}"
    latest_hsm_config_folder = f"{archive_run_prefix}/{latest_hsm_config_table}"

    # bucket-owner-full-control: these 3 files get read back by Starburst as external
    # tables, which authenticates as the bucket owner — without this ACL, an object
    # uploaded by a different AWS account/role is invisible to that read path (see the
    # SMUD HIVE_CANNOT_OPEN_SPLIT failure). Uploads elsewhere in this codebase (MySQL
    # exports, archived results) are never read by Starburst and don't need this.
    full_s3_uri = upload_s3_file(
        full_csv,
        table_stage_s3_config.bucket,
        f"{full_folder}/{full_csv.name}",
        table_stage_s3_config.region,
        acl="bucket-owner-full-control",
        assume_role_arn=table_stage_s3_config.assume_role_arn,
    )
    print(f"[{pilot.display_name}]   uploaded {full_csv.name} -> {full_s3_uri}")
    checkforev_zero_s3_uri = upload_s3_file(
        checkforev_zero_csv,
        table_stage_s3_config.bucket,
        f"{checkforev_zero_folder}/{checkforev_zero_csv.name}",
        table_stage_s3_config.region,
        acl="bucket-owner-full-control",
        assume_role_arn=table_stage_s3_config.assume_role_arn,
    )
    print(f"[{pilot.display_name}]   uploaded {checkforev_zero_csv.name} -> {checkforev_zero_s3_uri}")
    latest_hsm_config_s3_uri = upload_s3_file(
        latest_hsm_config_csv,
        table_stage_s3_config.bucket,
        f"{latest_hsm_config_folder}/{latest_hsm_config_csv.name}",
        table_stage_s3_config.region,
        acl="bucket-owner-full-control",
        assume_role_arn=table_stage_s3_config.assume_role_arn,
    )
    print(f"[{pilot.display_name}]   uploaded {latest_hsm_config_csv.name} -> {latest_hsm_config_s3_uri}")

    print(f"[{pilot.display_name}] Step 2/4: pointing test_db tables at the uploaded CSVs...")
    with connection.cursor() as cursor:
        print(f"[{pilot.display_name}]   loading test_db.{full_table} ({len(full_meter_ids)} rows)")
        _create_external_csv_table(
            cursor, full_table, "meterid VARCHAR", f"s3://{table_stage_s3_config.bucket}/{full_folder}/"
        )

        print(
            f"[{pilot.display_name}]   loading test_db.{checkforev_zero_table} "
            f"({len(checkforev_zero_meter_ids)} rows)"
        )
        _create_external_csv_table(
            cursor,
            checkforev_zero_table,
            "meterid VARCHAR",
            f"s3://{table_stage_s3_config.bucket}/{checkforev_zero_folder}/",
        )

        print(
            f"[{pilot.display_name}]   loading test_db.{latest_hsm_config_table} "
            f"({latest_hsm_config_count} rows)"
        )
        _create_external_csv_table(
            cursor,
            latest_hsm_config_table,
            "meterid VARCHAR, bidgelymeterid VARCHAR, evmin VARCHAR, evmax VARCHAR",
            f"s3://{table_stage_s3_config.bucket}/{latest_hsm_config_folder}/",
        )

    connection.commit()
    print(f"[{pilot.display_name}]   Starburst staging complete.")
    return full_table, checkforev_zero_table, latest_hsm_config_table


def run_redshift_analysis(
    *,
    config,
    s3_stage_config,
    table_stage_s3_config,
    pilot: PilotDefinition,
    output_dir: Path,
    archive_run_prefix: str | None = None,
    table_name_prefix: str | None = None,
) -> PilotAnalysisSummary:
    pilot_output_dir = output_dir / pilot.key
    analysis_output_dir = pilot_output_dir / "analysis"
    analysis_output_dir.mkdir(parents=True, exist_ok=True)
    date_str = output_dir.name if output_dir.name.isdigit() and len(output_dir.name) == 8 else datetime.now().strftime("%Y%m%d")
    archive_run_prefix = archive_run_prefix or build_archive_run_prefix(
        base_prefix=s3_stage_config.prefix,
        pilot_key=pilot.key,
        date_str=date_str,
        timestamp=datetime.now().strftime("%H%M%S"),
        request_name=_sanitize_table_fragment(table_name_prefix) if table_name_prefix else None,
    )
    delete_s3_prefix_older_than_days(
        s3_stage_config.bucket,
        f"{s3_stage_config.prefix.strip('/')}/results/",
        s3_stage_config.region,
    )
    archive_folder_s3_uri = f"s3://{s3_stage_config.bucket}/{archive_run_prefix}/"

    with redshift_connection(config) as connection:
        full_table, checkforev_zero_table, latest_hsm_config_table = _stage_tables_for_pilot(
            connection,
            pilot,
            output_dir,
            table_stage_s3_config,
            archive_run_prefix,
            table_name_prefix,
        )
        with connection.cursor() as cursor:
            total_user_count = _fetch_scalar_count(cursor, pilot_user_count_sql(pilot.pilot_id))
            print(f"[{pilot.display_name}]   Full User Count: {total_user_count}")

            print(f"[{pilot.display_name}] Step 3/4: calculating HSM+HAS completed bucket...")
            hsm_has_completed = _fetch_meter_ids(cursor, hsm_has_completed_sql(full_table, latest_hsm_config_table))
            print(f"[{pilot.display_name}]   HSM+HAS completed: {len(hsm_has_completed)}")

            print(f"[{pilot.display_name}] Step 4/4: calculating retry buckets...")
            hsm_completed = _fetch_meter_ids(cursor, hsm_completed_sql(full_table, latest_hsm_config_table))
            print(f"[{pilot.display_name}]   HSM completed: {len(hsm_completed)}")
            has_completed = _fetch_meter_ids(cursor, has_completed_sql(full_table, latest_hsm_config_table))
            print(f"[{pilot.display_name}]   HAS completed: {len(has_completed)}")
            has_retry = _fetch_meter_ids(cursor, has_retry_sql(full_table, latest_hsm_config_table))
            print(f"[{pilot.display_name}]   HAS retry: {len(has_retry)}")
            hsm_retry = _fetch_meter_ids(cursor, hsm_retry_sql(full_table, checkforev_zero_table, latest_hsm_config_table))
            print(f"[{pilot.display_name}]   HSM retry: {len(hsm_retry)}")
            leftovers = _fetch_meter_ids(cursor, leftovers_sql(full_table, checkforev_zero_table))
            print(f"[{pilot.display_name}]   leftovers: {len(leftovers)}")

    hsm_has_completed_result = _write_result(
        analysis_output_dir,
        f"{pilot.key}_hsm_has_completed.csv",
        hsm_has_completed,
    )
    hsm_completed_result = _write_result(
        analysis_output_dir,
        f"{pilot.key}_hsm_completed.csv",
        hsm_completed,
    )
    has_completed_result = _write_result(
        analysis_output_dir,
        f"{pilot.key}_has_completed.csv",
        has_completed,
    )
    has_retry_result = _write_result(
        analysis_output_dir,
        f"{pilot.key}_has_retry.csv",
        has_retry,
    )
    hsm_retry_result = _write_result(
        analysis_output_dir,
        f"{pilot.key}_hsm_retry.csv",
        hsm_retry,
    )
    leftovers_result = _write_result(
        analysis_output_dir,
        f"{pilot.key}_leftovers.csv",
        leftovers,
    )

    print(f"[{pilot.display_name}]   uploading analysis meter lists to S3 for archive...")
    hsm_has_completed_result = AnalysisResult(
        hsm_has_completed_result.file_path,
        hsm_has_completed_result.row_count,
        _upload_analysis_result(hsm_has_completed_result.file_path, s3_stage_config, archive_run_prefix),
    )
    hsm_completed_result = AnalysisResult(
        hsm_completed_result.file_path,
        hsm_completed_result.row_count,
        _upload_analysis_result(hsm_completed_result.file_path, s3_stage_config, archive_run_prefix),
    )
    has_completed_result = AnalysisResult(
        has_completed_result.file_path,
        has_completed_result.row_count,
        _upload_analysis_result(has_completed_result.file_path, s3_stage_config, archive_run_prefix),
    )
    has_retry_result = AnalysisResult(
        has_retry_result.file_path,
        has_retry_result.row_count,
        _upload_analysis_result(has_retry_result.file_path, s3_stage_config, archive_run_prefix),
    )
    hsm_retry_result = AnalysisResult(
        hsm_retry_result.file_path,
        hsm_retry_result.row_count,
        _upload_analysis_result(hsm_retry_result.file_path, s3_stage_config, archive_run_prefix),
    )
    leftovers_result = AnalysisResult(
        leftovers_result.file_path,
        leftovers_result.row_count,
        _upload_analysis_result(leftovers_result.file_path, s3_stage_config, archive_run_prefix),
    )

    return PilotAnalysisSummary(
        hsm_has_completed=hsm_has_completed_result,
        hsm_completed=hsm_completed_result,
        has_completed=has_completed_result,
        has_retry=has_retry_result,
        hsm_retry=hsm_retry_result,
        leftovers=leftovers_result,
        archive_folder_s3_uri=archive_folder_s3_uri,
        total_user_count=total_user_count,
    )
