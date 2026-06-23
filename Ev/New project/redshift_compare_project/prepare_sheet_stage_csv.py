from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert the sheet CSV into Redshift stage CSV format.")
    parser.add_argument("--sheet-csv", required=True, help="Path to the exported source sheet CSV.")
    parser.add_argument("--output-csv", required=True, help="Path to write the Redshift-ready CSV.")
    return parser.parse_args()


def parse_sheet_timestamp(value: str) -> int:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def main() -> int:
    args = parse_args()
    input_path = Path(args.sheet_csv)
    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open(newline="", encoding="utf-8-sig") as src, output_path.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        required = {"bidgelyId", "start", "end", "value_wh"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("Sheet CSV must contain bidgelyId, start, end, and value_wh columns")

        writer = csv.DictWriter(
            dst,
            fieldnames=["bidgely_id", "start_epoch", "end_epoch", "sheet_value"],
        )
        writer.writeheader()

        row_count = 0
        for row in reader:
            bidgely_id = (row.get("bidgelyId") or "").strip()
            start = (row.get("start") or "").strip()
            end = (row.get("end") or "").strip()
            value = (row.get("value_wh") or "").strip()
            if not bidgely_id and not start and not end and not value:
                continue
            writer.writerow(
                {
                    "bidgely_id": bidgely_id,
                    "start_epoch": parse_sheet_timestamp(start),
                    "end_epoch": parse_sheet_timestamp(end),
                    "sheet_value": value,
                }
            )
            row_count += 1

    print(f"Wrote {row_count} rows -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
