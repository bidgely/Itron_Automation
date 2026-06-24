from __future__ import annotations

import argparse
import csv
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract unique bidgelyId values from a sheet CSV.")
    parser.add_argument("--sheet-csv", required=True, help="Path to the exported sheet CSV.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sheet_csv = Path(args.sheet_csv)
    with sheet_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "bidgelyId" not in reader.fieldnames:
            raise ValueError("Sheet CSV must contain a 'bidgelyId' column")
        uuids = sorted({(row.get("bidgelyId") or "").strip() for row in reader if (row.get("bidgelyId") or "").strip()})

    print(f"Unique UUID count: {len(uuids)}")
    for value in uuids:
        print(value)

    print("\nAthena filter:\n")
    quoted = ",\n".join(f"    '{value}'" for value in uuids)
    print("(\n" + quoted + "\n)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
