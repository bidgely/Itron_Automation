from __future__ import annotations

import argparse
import csv
import gzip
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import boto3


@dataclass(frozen=True)
class TargetRow:
    bidgely_id: str
    start_epoch: int
    end_epoch: int

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.bidgely_id, self.start_epoch, self.end_epoch)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract matching JSON rows from S3 into a Redshift-ready CSV.")
    parser.add_argument("--sheet-stage-csv", required=True, help="Prepared sheet_stage.csv path.")
    parser.add_argument("--s3-prefix-root", required=True, help="Root S3 prefix before date/hour/minute partitions.")
    parser.add_argument("--region", required=True, help="AWS region.")
    parser.add_argument("--output-csv", required=True, help="Destination CSV path.")
    parser.add_argument("--max-workers", type=int, default=12, help="Maximum worker threads.")
    return parser.parse_args()


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def read_sheet_targets(sheet_stage_csv: Path) -> dict[str, set[tuple[str, int, int]]]:
    prefix_targets: dict[str, set[tuple[str, int, int]]] = {}
    with sheet_stage_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"bidgely_id", "start_epoch", "end_epoch", "sheet_value"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("sheet_stage.csv must contain bidgely_id, start_epoch, end_epoch, sheet_value")
        for row in reader:
            target = TargetRow(
                bidgely_id=(row.get("bidgely_id") or "").strip(),
                start_epoch=int((row.get("start_epoch") or "").strip()),
                end_epoch=int((row.get("end_epoch") or "").strip()),
            )
            prefix = build_minute_prefix(target.start_epoch)
            prefix_targets.setdefault(prefix, set()).add(target.key)
    return prefix_targets


def build_minute_prefix(start_epoch: int) -> str:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    return f"date={dt.strftime('%Y-%m-%d')}/hour={dt.strftime('%H')}/minute={dt.strftime('%M')}/"


def list_s3_keys(client, bucket: str, prefix: str) -> list[str]:
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item.get("Key")
            if key and not key.endswith("/"):
                keys.append(key)
    return keys


def read_s3_text(client, bucket: str, key: str) -> str:
    response = client.get_object(Bucket=bucket, Key=key)
    payload = response["Body"].read()
    if key.endswith(".gz"):
        payload = gzip.decompress(payload)
    return payload.decode("utf-8")


def scan_prefix(
    bucket: str,
    full_prefix: str,
    target_keys: set[tuple[str, int, int]],
    region: str,
) -> list[dict[str, str]]:
    session = boto3.session.Session(region_name=region)
    client = session.client("s3")
    keys = [
        key
        for key in list_s3_keys(client, bucket, full_prefix)
        if key.endswith((".json", ".json.gz", ".ndjson", ".ndjson.gz"))
    ]
    matched_rows: list[dict[str, str]] = []
    remaining = set(target_keys)
    for key in keys:
        if not remaining:
            break
        text = read_s3_text(client, bucket, key)
        for line in text.splitlines():
            line = line.strip()
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
                row_key = (bidgely_id, int(start), int(end))
                if row_key not in remaining:
                    continue
                matched_rows.append(
                    {
                        "bidgely_id": bidgely_id,
                        "start_epoch": str(int(start)),
                        "end_epoch": str(int(end)),
                        "json_value": str(value),
                    }
                )
                remaining.discard(row_key)
    print(
        f"finished {full_prefix} | matched {len(matched_rows)}/{len(target_keys)}"
    )
    return matched_rows


def main() -> int:
    args = parse_args()
    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    bucket, root_prefix = parse_s3_uri(args.s3_prefix_root)
    root_prefix = root_prefix.rstrip("/")
    prefix_targets = read_sheet_targets(Path(args.sheet_stage_csv))
    full_targets = {
        f"{root_prefix}/{relative_prefix}": target_keys
        for relative_prefix, target_keys in prefix_targets.items()
    }

    all_rows: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(args.max_workers, len(full_targets) or 1)) as executor:
        future_map = {
            executor.submit(scan_prefix, bucket, full_prefix, target_keys, args.region): full_prefix
            for full_prefix, target_keys in full_targets.items()
        }
        for future in as_completed(future_map):
            all_rows.extend(future.result())

    all_rows.sort(key=lambda row: (row["bidgely_id"], int(row["start_epoch"]), int(row["end_epoch"])))
    seen: set[tuple[str, str, str]] = set()
    deduped_rows: list[dict[str, str]] = []
    for row in all_rows:
        key = (row["bidgely_id"], row["start_epoch"], row["end_epoch"])
        if key in seen:
            continue
        seen.add(key)
        deduped_rows.append(row)

    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["bidgely_id", "start_epoch", "end_epoch", "json_value"])
        writer.writeheader()
        writer.writerows(deduped_rows)

    print(f"Wrote {len(deduped_rows)} rows -> {output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
