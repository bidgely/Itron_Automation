from __future__ import annotations

import csv
from pathlib import Path


def write_csv_rows(output_path: Path, headers: list[str], rows: list[list[str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)


def write_single_column_csv(output_path: Path, header: str, values: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([header])
        for value in values:
            writer.writerow([value])


def read_single_column_csv(input_path: Path) -> list[str]:
    with input_path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.reader(csv_file)
        rows = list(reader)

    if not rows:
        return []

    data_rows = rows
    if rows[0] and rows[0][0].strip().lower() == "meterid":
        data_rows = rows[1:]

    values = [row[0].strip() for row in data_rows if row and row[0].strip()]
    return list(dict.fromkeys(values))
