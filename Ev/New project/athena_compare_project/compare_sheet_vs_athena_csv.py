from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare sheet CSV rows against Athena query CSV output.")
    parser.add_argument("--sheet-csv", required=True, help="Path to the exported sheet CSV.")
    parser.add_argument("--athena-csv", required=True, help="Path to the Athena query result CSV.")
    parser.add_argument("--output-dir", default="athena_compare_project/output", help="Directory for CSV reports.")
    parser.add_argument("--value-tolerance", default="0.000001", help="Allowed absolute difference between values.")
    return parser.parse_args()


def parse_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc


def parse_sheet_timestamp(value: str) -> int:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def read_sheet_records(sheet_csv: Path) -> list[Record]:
    with sheet_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"bidgelyId", "start", "end", "value_wh"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("Sheet CSV must contain bidgelyId, start, end, and value_wh columns")
        records: list[Record] = []
        for index, row in enumerate(reader, start=2):
            uuid = (row.get("bidgelyId") or "").strip()
            start = (row.get("start") or "").strip()
            end = (row.get("end") or "").strip()
            value = row.get("value_wh")
            if not uuid and not start and not end and not value:
                continue
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


def read_athena_records(athena_csv: Path) -> list[Record]:
    with athena_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"bidgely_id", "start_epoch", "end_epoch", "json_value"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("Athena CSV must contain bidgely_id, start_epoch, end_epoch, and json_value columns")
        records: list[Record] = []
        for index, row in enumerate(reader, start=2):
            uuid = (row.get("bidgely_id") or "").strip()
            start_epoch = (row.get("start_epoch") or "").strip()
            end_epoch = (row.get("end_epoch") or "").strip()
            json_value = row.get("json_value")
            if not uuid and not start_epoch and not end_epoch and not json_value:
                continue
            records.append(
                Record(
                    uuid=uuid,
                    start_epoch=int(start_epoch),
                    end_epoch=int(end_epoch),
                    value=parse_decimal(json_value),
                    source=f"athena row {index}",
                )
            )
    return records


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
    tolerance = parse_decimal(args.value_tolerance)
    output_dir = Path(args.output_dir)

    sheet_map = build_record_map(read_sheet_records(Path(args.sheet_csv)), "sheet")
    athena_map = build_record_map(read_athena_records(Path(args.athena_csv)), "Athena")

    sheet_keys = set(sheet_map)
    athena_keys = set(athena_map)
    shared_keys = sorted(sheet_keys & athena_keys)
    sheet_only_keys = sorted(sheet_keys - athena_keys)
    athena_only_keys = sorted(athena_keys - sheet_keys)

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
    athena_only_rows = [
        {
            "uuid": record.uuid,
            "start_epoch": str(record.start_epoch),
            "end_epoch": str(record.end_epoch),
            "athena_value": str(record.value),
            "athena_source": record.source,
        }
        for key in athena_only_keys
        for record in [athena_map[key]]
    ]
    mismatch_rows: list[dict[str, str]] = []
    for key in shared_keys:
        sheet_record = sheet_map[key]
        athena_record = athena_map[key]
        difference = abs(sheet_record.value - athena_record.value)
        if difference > tolerance:
            mismatch_rows.append(
                {
                    "uuid": sheet_record.uuid,
                    "start_epoch": str(sheet_record.start_epoch),
                    "end_epoch": str(sheet_record.end_epoch),
                    "sheet_value": str(sheet_record.value),
                    "athena_value": str(athena_record.value),
                    "difference": str(difference),
                    "sheet_source": sheet_record.source,
                    "athena_source": athena_record.source,
                }
            )

    sheet_only_path = output_dir / "sheet_only.csv"
    athena_only_path = output_dir / "athena_only.csv"
    mismatch_path = output_dir / "value_mismatches.csv"
    write_csv(sheet_only_path, sheet_only_rows, ["uuid", "start_epoch", "end_epoch", "sheet_value", "sheet_source"])
    write_csv(
        athena_only_path,
        athena_only_rows,
        ["uuid", "start_epoch", "end_epoch", "athena_value", "athena_source"],
    )
    write_csv(
        mismatch_path,
        mismatch_rows,
        ["uuid", "start_epoch", "end_epoch", "sheet_value", "athena_value", "difference", "sheet_source", "athena_source"],
    )

    print(f"Sheet-only rows: {len(sheet_only_rows)} -> {sheet_only_path}")
    print(f"Athena-only rows: {len(athena_only_rows)} -> {athena_only_path}")
    print(f"Value mismatches: {len(mismatch_rows)} -> {mismatch_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
