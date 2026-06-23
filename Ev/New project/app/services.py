from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .db import mysql_connection
from .exporters import read_single_column_csv, write_csv_rows, write_single_column_csv
from .mysql_queries import (
    CHECK_FOR_EV_ZERO_SQL,
    FULL_METER_LIST_SQL,
    LATEST_HSM_EV_CONFIG_SQL,
    REQUEST_SENT_LIST_SQL,
)
from .s3_utils import (
    build_archive_run_prefix,
    delete_s3_prefix_older_than_days,
    download_s3_file,
    upload_s3_file,
)


@dataclass(frozen=True)
class ExportResult:
    file_path: Path
    row_count: int
    s3_uri: str | None = None


@dataclass(frozen=True)
class PilotExportSummary:
    full_list: ExportResult
    request_sent_list: ExportResult
    checkforev_zero_list: ExportResult
    effective_list: ExportResult
    latest_hsm_config: ExportResult
    null_config_count: int
    archive_folder_s3_uri: str


def _fetch_meter_ids(connection, sql: str, params: tuple) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [row["meterid"] for row in rows if row["meterid"]]


def _fetch_latest_hsm_config_rows(connection, pilot_id: int) -> list[list[str]]:
    with connection.cursor() as cursor:
        cursor.execute(LATEST_HSM_EV_CONFIG_SQL, (pilot_id,))
        rows = cursor.fetchall()
    return [
        [
            str(row["meterid"]).strip(),
            str(row["bidgelymeterid"]).strip(),
            str(row["evmin"]).strip(),
            str(row["evmax"]).strip(),
        ]
        for row in rows
        if row["meterid"] and row["bidgelymeterid"] and row["evmin"] and row["evmax"]
    ]


def _upload_meter_csv(local_path: Path, s3_stage_config, archive_run_prefix: str) -> str:
    key = f"{archive_run_prefix}/{local_path.name}"
    return upload_s3_file(local_path, s3_stage_config.bucket, key, s3_stage_config.region)


def export_mysql_meter_lists(
    *,
    config,
    s3_stage_config,
    pilot_id: int,
    pilot_name: str,
    output_dir: Path,
    checkforev_zero_min_id: int,
    archive_run_prefix: str | None = None,
) -> PilotExportSummary:
    pilot_output_dir = output_dir / pilot_name.lower()
    date_str = output_dir.name if output_dir.name.isdigit() and len(output_dir.name) == 8 else datetime.now().strftime("%Y%m%d")
    archive_run_prefix = archive_run_prefix or build_archive_run_prefix(
        base_prefix=s3_stage_config.prefix,
        pilot_key=pilot_name.lower(),
        date_str=date_str,
        timestamp=datetime.now().strftime("%H%M%S"),
    )
    delete_s3_prefix_older_than_days(
        s3_stage_config.bucket,
        f"{s3_stage_config.prefix.strip('/')}/results/",
        s3_stage_config.region,
    )
    archive_folder_s3_uri = f"s3://{s3_stage_config.bucket}/{archive_run_prefix}/"
    print(f"[{pilot_name}] Step 1/3: connecting to MySQL and extracting meter lists...")

    with mysql_connection(config) as connection:
        full_list = _fetch_meter_ids(connection, FULL_METER_LIST_SQL, (pilot_id,))
        print(f"[{pilot_name}]   extracted full meter list: {len(full_list)}")
        request_sent_list = _fetch_meter_ids(connection, REQUEST_SENT_LIST_SQL, (pilot_id,))
        print(f"[{pilot_name}]   extracted request-sent list: {len(request_sent_list)}")
        checkforev_zero_list = _fetch_meter_ids(
            connection,
            CHECK_FOR_EV_ZERO_SQL,
            (pilot_id, checkforev_zero_min_id),
        )
        print(f"[{pilot_name}]   extracted CheckForEV=0 list: {len(checkforev_zero_list)}")
        latest_hsm_config_rows = _fetch_latest_hsm_config_rows(connection, pilot_id)

    full_list_path = pilot_output_dir / f"{pilot_name.lower()}_full_list_meters.csv"
    request_sent_path = pilot_output_dir / f"{pilot_name.lower()}_request_sent_meters.csv"
    checkforev_zero_path = pilot_output_dir / f"{pilot_name.lower()}_checkforev0_meters.csv"
    effective_list_path = pilot_output_dir / f"{pilot_name.lower()}_effective_meters.csv"
    latest_hsm_config_path = pilot_output_dir / f"{pilot_name.lower()}_latest_hsm_ev_config.csv"
    checkforev_zero_set = set(checkforev_zero_list)
    latest_hsm_config_meter_ids = {row[0] for row in latest_hsm_config_rows}
    effective_list = [
        meter_id for meter_id in full_list
        if meter_id not in checkforev_zero_set
        and meter_id in latest_hsm_config_meter_ids
    ]
    null_config_count = sum(
        1 for m in full_list
        if m not in checkforev_zero_set
        and m not in latest_hsm_config_meter_ids
    )
    print(f"[{pilot_name}]   null configurations: {null_config_count}")
    print(f"[{pilot_name}]   effective meters: {len(effective_list)}")

    print(f"[{pilot_name}] Step 2/3: writing CSV files...")
    write_single_column_csv(full_list_path, "meterid", full_list)
    write_single_column_csv(request_sent_path, "meterid", request_sent_list)
    write_single_column_csv(checkforev_zero_path, "meterid", checkforev_zero_list)
    write_single_column_csv(effective_list_path, "meterid", effective_list)
    write_csv_rows(latest_hsm_config_path, ["meterid", "bidgelymeterid", "evmin", "evmax"], latest_hsm_config_rows)
    print(f"[{pilot_name}]   uploading meter lists to S3 for archive...")
    full_list_s3_uri = _upload_meter_csv(full_list_path, s3_stage_config, archive_run_prefix)
    request_sent_s3_uri = _upload_meter_csv(request_sent_path, s3_stage_config, archive_run_prefix)
    checkforev_zero_s3_uri = _upload_meter_csv(checkforev_zero_path, s3_stage_config, archive_run_prefix)
    effective_list_s3_uri = _upload_meter_csv(effective_list_path, s3_stage_config, archive_run_prefix)
    latest_hsm_config_s3_uri = _upload_meter_csv(latest_hsm_config_path, s3_stage_config, archive_run_prefix)
    print(f"[{pilot_name}] Step 3/3: MySQL export complete.")

    return PilotExportSummary(
        full_list=ExportResult(full_list_path, len(full_list), full_list_s3_uri),
        request_sent_list=ExportResult(request_sent_path, len(request_sent_list), request_sent_s3_uri),
        checkforev_zero_list=ExportResult(checkforev_zero_path, len(checkforev_zero_list), checkforev_zero_s3_uri),
        effective_list=ExportResult(effective_list_path, len(effective_list), effective_list_s3_uri),
        latest_hsm_config=ExportResult(latest_hsm_config_path, len(latest_hsm_config_rows), latest_hsm_config_s3_uri),
        null_config_count=null_config_count,
        archive_folder_s3_uri=archive_folder_s3_uri,
    )


def export_special_request_meter_lists(
    *,
    config,
    s3_stage_config,
    pilot_id: int,
    pilot_name: str,
    output_dir: Path,
    checkforev_zero_min_id: int,
    meter_list_s3_uri: str,
    aws_region: str,
    archive_run_prefix: str | None = None,
) -> PilotExportSummary:
    pilot_output_dir = output_dir / pilot_name.lower()
    date_str = datetime.now().strftime("%Y%m%d")
    archive_run_prefix = archive_run_prefix or build_archive_run_prefix(
        base_prefix=s3_stage_config.prefix,
        pilot_key=pilot_name.lower(),
        date_str=date_str,
        timestamp=datetime.now().strftime("%H%M%S"),
        request_name=output_dir.name,
    )
    delete_s3_prefix_older_than_days(
        s3_stage_config.bucket,
        f"{s3_stage_config.prefix.strip('/')}/results/",
        s3_stage_config.region,
    )
    archive_folder_s3_uri = f"s3://{s3_stage_config.bucket}/{archive_run_prefix}/"
    source_download_path = pilot_output_dir / "source" / "client_meter_list.csv"
    full_list_path = pilot_output_dir / f"{pilot_name.lower()}_full_list_meters.csv"
    request_sent_path = pilot_output_dir / f"{pilot_name.lower()}_request_sent_meters.csv"
    checkforev_zero_path = pilot_output_dir / f"{pilot_name.lower()}_checkforev0_meters.csv"
    effective_list_path = pilot_output_dir / f"{pilot_name.lower()}_effective_meters.csv"
    latest_hsm_config_path = pilot_output_dir / f"{pilot_name.lower()}_latest_hsm_ev_config.csv"

    print(f"[{pilot_name}] Step 1/4: downloading client meter list from S3...")
    download_s3_file(meter_list_s3_uri, source_download_path, aws_region)
    special_meter_ids = read_single_column_csv(source_download_path)
    if not special_meter_ids:
        raise ValueError(f"No meter ids found in client file: {meter_list_s3_uri}")
    special_meter_set = set(special_meter_ids)
    print(f"[{pilot_name}]   downloaded client list: {len(special_meter_ids)}")

    print(f"[{pilot_name}] Step 2/4: extracting CheckForEV=0 list from MySQL...")
    with mysql_connection(config) as connection:
        checkforev_zero_all = _fetch_meter_ids(
            connection,
            CHECK_FOR_EV_ZERO_SQL,
            (pilot_id, checkforev_zero_min_id),
        )
        latest_hsm_config_all = _fetch_latest_hsm_config_rows(connection, pilot_id)
    checkforev_zero_list = [meter_id for meter_id in checkforev_zero_all if meter_id in special_meter_set]
    latest_hsm_config_rows = [row for row in latest_hsm_config_all if row[0] in special_meter_set]
    print(f"[{pilot_name}]   filtered CheckForEV=0 list: {len(checkforev_zero_list)}")

    print(f"[{pilot_name}] Step 3/4: writing special-request CSV files...")
    checkforev_zero_set = set(checkforev_zero_list)
    latest_hsm_config_meter_ids = {row[0] for row in latest_hsm_config_rows}
    effective_list = [
        meter_id for meter_id in special_meter_ids
        if meter_id not in checkforev_zero_set
        and meter_id in latest_hsm_config_meter_ids
    ]
    null_config_count = sum(
        1 for m in special_meter_ids
        if m not in checkforev_zero_set
        and m not in latest_hsm_config_meter_ids
    )
    print(f"[{pilot_name}]   null configurations: {null_config_count}")
    print(f"[{pilot_name}]   effective meters: {len(effective_list)}")
    write_single_column_csv(full_list_path, "meterid", special_meter_ids)
    write_single_column_csv(request_sent_path, "meterid", [])
    write_single_column_csv(checkforev_zero_path, "meterid", checkforev_zero_list)
    write_single_column_csv(effective_list_path, "meterid", effective_list)
    write_csv_rows(latest_hsm_config_path, ["meterid", "bidgelymeterid", "evmin", "evmax"], latest_hsm_config_rows)
    print(f"[{pilot_name}]   uploading meter lists to S3 for archive...")
    full_list_s3_uri = _upload_meter_csv(full_list_path, s3_stage_config, archive_run_prefix)
    request_sent_s3_uri = _upload_meter_csv(request_sent_path, s3_stage_config, archive_run_prefix)
    checkforev_zero_s3_uri = _upload_meter_csv(checkforev_zero_path, s3_stage_config, archive_run_prefix)
    effective_list_s3_uri = _upload_meter_csv(effective_list_path, s3_stage_config, archive_run_prefix)
    latest_hsm_config_s3_uri = _upload_meter_csv(latest_hsm_config_path, s3_stage_config, archive_run_prefix)

    print(f"[{pilot_name}] Step 4/4: special-request list preparation complete.")
    return PilotExportSummary(
        full_list=ExportResult(full_list_path, len(special_meter_ids), full_list_s3_uri),
        request_sent_list=ExportResult(request_sent_path, 0, request_sent_s3_uri),
        checkforev_zero_list=ExportResult(checkforev_zero_path, len(checkforev_zero_list), checkforev_zero_s3_uri),
        effective_list=ExportResult(effective_list_path, len(effective_list), effective_list_s3_uri),
        latest_hsm_config=ExportResult(latest_hsm_config_path, len(latest_hsm_config_rows), latest_hsm_config_s3_uri),
        null_config_count=null_config_count,
        archive_folder_s3_uri=archive_folder_s3_uri,
    )
