from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import boto3


@dataclass(frozen=True)
class Record:
    uuid: str
    start_epoch: int
    end_epoch: int
    value: Decimal
    source: str

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.uuid, self.start_epoch, self.end_epoch)


@dataclass(frozen=True)
class PrefixScanResult:
    matched_records: list[Record]
    scanned_files: int
    total_files_in_prefix: int
    prefix: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a Google Sheet tab against JSON files in S3."
    )
    parser.add_argument(
        "--spreadsheet-url",
        help="Google Sheets URL with gid. Works only when the tab can be exported by the current environment.",
    )
    parser.add_argument(
        "--sheet-csv",
        help="Local CSV export of the target Google Sheet tab. Use this for private sheets.",
    )
    parser.add_argument("--region", required=True, help="AWS region for the S3 bucket.")
    parser.add_argument(
        "--s3-prefix-root",
        required=True,
        help="Root S3 prefix before date/hour partitions, for example s3://bucket/solar_usage_data/duration=5min/",
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="End date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--output-dir",
        default="sheet_compare_project/output",
        help="Directory for generated CSV reports.",
    )
    parser.add_argument(
        "--value-tolerance",
        default="0.000001",
        help="Allowed absolute difference between sheet and JSON values.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=12,
        help="Maximum number of worker threads for targeted minute-prefix scans.",
    )
    args = parser.parse_args()
    if not args.spreadsheet_url and not args.sheet_csv:
        parser.error("one of --spreadsheet-url or --sheet-csv is required")
    if args.max_workers < 1:
        parser.error("--max-workers must be at least 1")
    return args


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def build_date_prefixes(s3_prefix_root: str, start_date_text: str, end_date_text: str) -> list[str]:
    start_date = date.fromisoformat(start_date_text)
    end_date = date.fromisoformat(end_date_text)
    if end_date < start_date:
        raise ValueError("end-date must be on or after start-date")

    bucket, prefix = parse_s3_uri(s3_prefix_root)
    normalized_prefix = prefix.rstrip("/")
    prefixes: list[str] = []
    current = start_date
    while current <= end_date:
        prefixes.append(f"s3://{bucket}/{normalized_prefix}/date={current.isoformat()}/")
        current += timedelta(days=1)
    return prefixes


def build_minute_prefix(s3_prefix_root: str, start_epoch: int) -> str:
    bucket, prefix = parse_s3_uri(s3_prefix_root)
    normalized_prefix = prefix.rstrip("/")
    dt = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    return (
        f"s3://{bucket}/{normalized_prefix}/"
        f"date={dt.strftime('%Y-%m-%d')}/hour={dt.strftime('%H')}/minute={dt.strftime('%M')}/"
    )


def list_s3_keys(s3_uri: str, region: str) -> list[str]:
    bucket, prefix = parse_s3_uri(s3_uri)
    client = boto3.session.Session(region_name=region).client("s3")
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item.get("Key")
            if key and not key.endswith("/"):
                keys.append(key)
    return keys


def read_s3_text(s3_uri: str, region: str) -> str:
    bucket, key = parse_s3_uri(s3_uri)
    client = boto3.session.Session(region_name=region).client("s3")
    response = client.get_object(Bucket=bucket, Key=key)
    payload = response["Body"].read()
    if key.endswith(".gz"):
        payload = gzip.decompress(payload)
    return payload.decode("utf-8")


def parse_sheet_export_url(spreadsheet_url: str) -> str:
    parsed = urlparse(spreadsheet_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    try:
        spreadsheet_id = path_parts[path_parts.index("d") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Unsupported Google Sheets URL: {spreadsheet_url}") from exc

    query = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    gid = query.get("gid", fragment.get("gid", ["0"]))[0]
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"


def parse_sheet_timestamp(value: str) -> int:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def parse_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc


def _read_sheet_records_from_csv_text(raw_csv: str) -> list[Record]:
    reader = csv.DictReader(io.StringIO(raw_csv))
    required_columns = {"bidgelyId", "start", "end", "value_wh"}
    if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
        raise ValueError("Sheet must contain bidgelyId, start, end, and value_wh columns")

    records: list[Record] = []
    for index, row in enumerate(reader, start=2):
        uuid = (row.get("bidgelyId") or "").strip()
        start = (row.get("start") or "").strip()
        end = (row.get("end") or "").strip()
        value = row.get("value_wh")
        if not uuid and not start and not end and not value:
            continue
        if not uuid or not start or not end or value in (None, ""):
            raise ValueError(f"Incomplete sheet row at line {index}")
        records.append(
            Record(
                uuid=uuid,
                start_epoch=parse_sheet_timestamp(start),
                end_epoch=parse_sheet_timestamp(end),
                value=parse_decimal(value),
                source=f"sheet row {index}",
            )
        )
    return records


def read_sheet_records(*, spreadsheet_url: str | None, sheet_csv: str | None) -> list[Record]:
    if sheet_csv:
        raw_csv = Path(sheet_csv).read_text(encoding="utf-8-sig")
        return _read_sheet_records_from_csv_text(raw_csv)

    if not spreadsheet_url:
        raise ValueError("spreadsheet_url is required when sheet_csv is not provided")

    export_url = parse_sheet_export_url(spreadsheet_url)
    with urlopen(export_url) as response:
        raw_csv = response.read().decode("utf-8-sig")
    return _read_sheet_records_from_csv_text(raw_csv)


def iter_json_records(text: str, source_name: str) -> list[Record]:
    records: list[Record] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        request = payload.get("request") or {}
        bidgely_id = str(request.get("bidgelyId") or "").strip()
        if not bidgely_id:
            raise ValueError(f"Missing request.bidgelyId in {source_name}:{line_number}")
        for item_index, entry in enumerate(payload.get("payload") or [], start=1):
            start = entry.get("start")
            end = entry.get("end")
            value = entry.get("value")
            if start is None or end is None or value is None:
                raise ValueError(
                    f"Missing start/end/value in {source_name}:{line_number} payload item {item_index}"
                )
            records.append(
                Record(
                    uuid=bidgely_id,
                    start_epoch=int(start),
                    end_epoch=int(end),
                    value=parse_decimal(value),
                    source=f"{source_name}:{line_number}",
                )
            )
    return records


def iter_matching_json_records(
    text: str,
    source_name: str,
    target_keys: set[tuple[str, int, int]],
) -> list[Record]:
    if not target_keys:
        return []
    records: list[Record] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        request = payload.get("request") or {}
        bidgely_id = str(request.get("bidgelyId") or "").strip()
        if not bidgely_id:
            continue
        for entry in payload.get("payload") or []:
            start = entry.get("start")
            end = entry.get("end")
            value = entry.get("value")
            if start is None or end is None or value is None:
                continue
            candidate_key = (bidgely_id, int(start), int(end))
            if candidate_key not in target_keys:
                continue
            records.append(
                Record(
                    uuid=bidgely_id,
                    start_epoch=int(start),
                    end_epoch=int(end),
                    value=parse_decimal(value),
                    source=f"{source_name}:{line_number}",
                )
            )
    return records


def _list_prefix_keys(prefix: str, region: str, prefix_index: int, total_prefixes: int) -> tuple[str, list[str]]:
    print(f"  listing prefix {prefix_index}/{total_prefixes}: {prefix}")
    keys = [
        key
        for key in list_s3_keys(prefix, region)
        if key.endswith((".json", ".json.gz", ".ndjson", ".ndjson.gz"))
    ]
    print(f"    found candidate files: {len(keys)}")
    return prefix, keys


def _scan_target_prefix(
    prefix: str,
    keys: list[str],
    region: str,
    target_keys: set[tuple[str, int, int]],
) -> PrefixScanResult:
    bucket, _ = parse_s3_uri(prefix)
    remaining_keys = set(target_keys)
    matched_records: list[Record] = []
    scanned_files = 0
    for key in keys:
        if not remaining_keys:
            break
        scanned_files += 1
        file_uri = f"s3://{bucket}/{key}"
        file_matches = iter_matching_json_records(read_s3_text(file_uri, region), key, remaining_keys)
        if not file_matches:
            continue
        matched_records.extend(file_matches)
        for record in file_matches:
            remaining_keys.discard(record.key)
    return PrefixScanResult(
        matched_records=matched_records,
        scanned_files=scanned_files,
        total_files_in_prefix=len(keys),
        prefix=prefix,
    )


def read_targeted_json_records_from_s3(
    prefix_targets: dict[str, set[tuple[str, int, int]]],
    region: str,
    max_workers: int,
) -> tuple[list[Record], int]:
    prefixes = sorted(prefix_targets)
    listed_results: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(prefixes) or 1)) as executor:
        future_map = {
            executor.submit(_list_prefix_keys, prefix, region, index, len(prefixes)): prefix
            for index, prefix in enumerate(prefixes, start=1)
        }
        for future in as_completed(future_map):
            prefix, keys = future.result()
            listed_results[prefix] = keys

    total_candidate_files = sum(len(keys) for keys in listed_results.values())
    print(f"  total candidate files across target minute prefixes: {total_candidate_files}")
    if not listed_results:
        return [], 0

    records: list[Record] = []
    progress_lock = threading.Lock()
    completed_files = 0
    scanned_files_total = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, len(listed_results))) as executor:
        future_map = {
            executor.submit(
                _scan_target_prefix,
                prefix,
                listed_results[prefix],
                region,
                prefix_targets[prefix],
            ): prefix
            for prefix in listed_results
        }
        for future in as_completed(future_map):
            result = future.result()
            records.extend(result.matched_records)
            with progress_lock:
                scanned_files_total += result.scanned_files
                completed_files += len(result.matched_records)
                print(
                    f"    finished prefix: {result.prefix} | "
                    f"scanned {result.scanned_files}/{result.total_files_in_prefix} files | "
                    f"matched {len(result.matched_records)} records"
                )
    return records, scanned_files_total


def build_record_map(records: list[Record], label: str) -> dict[tuple[str, int, int], Record]:
    record_map: dict[tuple[str, int, int], Record] = {}
    duplicates: list[str] = []
    for record in records:
        if record.key in record_map:
            duplicates.append(f"{record.uuid}|{record.start_epoch}|{record.end_epoch}")
            continue
        record_map[record.key] = record
    if duplicates:
        sample = ", ".join(duplicates[:5])
        raise ValueError(f"Duplicate {label} keys found: {sample}")
    return record_map


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    tolerance = parse_decimal(args.value_tolerance)
    print("Step 1/4: reading sheet rows...")
    sheet_records = read_sheet_records(
        spreadsheet_url=args.spreadsheet_url,
        sheet_csv=args.sheet_csv,
    )
    print(f"  loaded sheet rows: {len(sheet_records)}")

    sheet_map = build_record_map(sheet_records, "sheet")
    prefix_targets: dict[str, set[tuple[str, int, int]]] = {}
    for record in sheet_records:
        prefix = build_minute_prefix(args.s3_prefix_root, record.start_epoch)
        prefix_targets.setdefault(prefix, set()).add(record.key)
    print(f"  unique UUIDs in sheet: {len({record.uuid for record in sheet_records})}")
    print(f"  target minute prefixes from sheet: {len(prefix_targets)}")

    print("Step 2/4: reading S3 JSON files...")
    json_records, processed_file_count = read_targeted_json_records_from_s3(
        prefix_targets,
        args.region,
        args.max_workers,
    )
    print(f"  processed JSON files: {processed_file_count}")
    print(f"  flattened JSON records: {len(json_records)}")

    print("Step 3/4: matching records by uuid + start + end...")
    json_map = build_record_map(json_records, "JSON")

    sheet_keys = set(sheet_map)
    json_keys = set(json_map)
    shared_keys = sorted(sheet_keys & json_keys)
    sheet_only_keys = sorted(sheet_keys - json_keys)

    mismatch_rows: list[dict[str, str]] = []
    for key in shared_keys:
        sheet_record = sheet_map[key]
        json_record = json_map[key]
        difference = abs(sheet_record.value - json_record.value)
        if difference > tolerance:
            mismatch_rows.append(
                {
                    "uuid": sheet_record.uuid,
                    "start_epoch": str(sheet_record.start_epoch),
                    "end_epoch": str(sheet_record.end_epoch),
                    "sheet_value": str(sheet_record.value),
                    "json_value": str(json_record.value),
                    "difference": str(difference),
                    "sheet_source": sheet_record.source,
                    "json_source": json_record.source,
                }
            )

    print("Step 4/4: writing comparison reports...")
    sheet_only_rows = [
        {
            "uuid": record.uuid,
            "start_epoch": str(record.start_epoch),
            "end_epoch": str(record.end_epoch),
            "sheet_value": str(record.value),
            "sheet_source": record.source,
        }
        for key in sheet_only_keys
        for record in [sheet_map[key]]
    ]
    json_only_rows: list[dict[str, str]] = []

    sheet_only_path = output_dir / "sheet_only.csv"
    json_only_path = output_dir / "json_only.csv"
    mismatch_path = output_dir / "value_mismatches.csv"

    write_csv(
        sheet_only_path,
        sheet_only_rows,
        ["uuid", "start_epoch", "end_epoch", "sheet_value", "sheet_source"],
    )
    write_csv(
        json_only_path,
        json_only_rows,
        ["uuid", "start_epoch", "end_epoch", "json_value", "json_source"],
    )
    write_csv(
        mismatch_path,
        mismatch_rows,
        [
            "uuid",
            "start_epoch",
            "end_epoch",
            "sheet_value",
            "json_value",
            "difference",
            "sheet_source",
            "json_source",
        ],
    )

    print(f"Matched keys: {len(shared_keys)}")
    print(f"Sheet-only rows: {len(sheet_only_rows)} -> {sheet_only_path}")
    print(f"JSON-only rows: {len(json_only_rows)} -> {json_only_path}")
    print(f"Value mismatches: {len(mismatch_rows)} -> {mismatch_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
