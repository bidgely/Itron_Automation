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


def _reset_stage_table(cursor, table_name: str) -> None:
    cursor.execute(f"CREATE TABLE IF NOT EXISTS test_db.{table_name} (meterid TEXT)")
    cursor.execute(f"TRUNCATE TABLE test_db.{table_name}")
    cursor.execute(f"GRANT ALL ON test_db.{table_name} TO PUBLIC")


def _reset_hsm_config_stage_table(cursor, table_name: str) -> None:
    cursor.execute(
        f"CREATE TABLE IF NOT EXISTS test_db.{table_name} "
        "(meterid TEXT, bidgelymeterid TEXT, evmin TEXT, evmax TEXT)"
    )
    cursor.execute(f"TRUNCATE TABLE test_db.{table_name}")
    cursor.execute(f"GRANT ALL ON test_db.{table_name} TO PUBLIC")


def _s3_key_for_csv(s3_stage_config, pilot: PilotDefinition, file_name: str) -> str:
    prefix = s3_stage_config.prefix.strip("/")
    return f"{prefix}/{pilot.key}/{file_name}"


def _copy_credentials_sql(s3_stage_config) -> str:
    credentials = (
        "aws_access_key_id="
        f"{s3_stage_config.access_key_id};aws_secret_access_key={s3_stage_config.secret_access_key}"
    )
    if s3_stage_config.session_token:
        credentials += f";token={s3_stage_config.session_token}"
    return credentials


def _copy_csv_into_table(cursor, table_name: str, s3_uri: str, s3_stage_config) -> None:
    copy_sql = f"""
COPY test_db.{table_name}
FROM '{s3_uri}'
CREDENTIALS '{_copy_credentials_sql(s3_stage_config)}'
REGION '{s3_stage_config.region}'
DELIMITER ','
IGNOREHEADER 1
EMPTYASNULL
TRIMBLANKS
BLANKSASNULL
MAXERROR 20
"""
    cursor.execute(copy_sql)


def _fetch_meter_ids(cursor, sql: str) -> list[str]:
    cursor.execute(sql)
    rows = cursor.fetchall()
    return [row[0] for row in rows if row and row[0]]


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
    s3_stage_config,
    archive_run_prefix: str,
    table_name_prefix: str | None = None,
) -> tuple[str, str]:
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

    print(f"[{pilot.display_name}] Step 1/4: uploading CSV files to S3 for fast Redshift COPY...")
    from .s3_utils import upload_s3_file

    full_s3_uri = upload_s3_file(
        full_csv,
        s3_stage_config.bucket,
        f"{archive_run_prefix}/{full_csv.name}",
        s3_stage_config.region,
    )
    print(f"[{pilot.display_name}]   uploaded {full_csv.name} -> {full_s3_uri}")
    checkforev_zero_s3_uri = upload_s3_file(
        checkforev_zero_csv,
        s3_stage_config.bucket,
        f"{archive_run_prefix}/{checkforev_zero_csv.name}",
        s3_stage_config.region,
    )
    print(f"[{pilot.display_name}]   uploaded {checkforev_zero_csv.name} -> {checkforev_zero_s3_uri}")
    latest_hsm_config_s3_uri = upload_s3_file(
        latest_hsm_config_csv,
        s3_stage_config.bucket,
        f"{archive_run_prefix}/{latest_hsm_config_csv.name}",
        s3_stage_config.region,
    )
    print(f"[{pilot.display_name}]   uploaded {latest_hsm_config_csv.name} -> {latest_hsm_config_s3_uri}")

    print(f"[{pilot.display_name}] Step 2/4: staging CSV data into Redshift test_db tables with COPY...")
    with connection.cursor() as cursor:
        print(f"[{pilot.display_name}]   loading test_db.{full_table} ({len(full_meter_ids)} rows)")
        _reset_stage_table(cursor, full_table)
        _copy_csv_into_table(cursor, full_table, full_s3_uri, s3_stage_config)

        print(
            f"[{pilot.display_name}]   loading test_db.{checkforev_zero_table} "
            f"({len(checkforev_zero_meter_ids)} rows)"
        )
        _reset_stage_table(cursor, checkforev_zero_table)
        if checkforev_zero_meter_ids:
            _copy_csv_into_table(cursor, checkforev_zero_table, checkforev_zero_s3_uri, s3_stage_config)

        print(
            f"[{pilot.display_name}]   loading test_db.{latest_hsm_config_table} "
            f"({latest_hsm_config_count} rows)"
        )
        _reset_hsm_config_stage_table(cursor, latest_hsm_config_table)
        if latest_hsm_config_count:
            _copy_csv_into_table(cursor, latest_hsm_config_table, latest_hsm_config_s3_uri, s3_stage_config)

    connection.commit()
    print(f"[{pilot.display_name}]   Redshift staging complete.")
    return full_table, checkforev_zero_table, latest_hsm_config_table


def run_redshift_analysis(
    *,
    config,
    s3_stage_config,
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
            s3_stage_config,
            archive_run_prefix,
            table_name_prefix,
        )
        with connection.cursor() as cursor:
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
    )
