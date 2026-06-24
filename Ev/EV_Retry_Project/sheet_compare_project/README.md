# Sheet vs S3 JSON Compare

This standalone tool compares rows from a Google Sheet tab against newline-delimited JSON files stored under an S3 prefix.

It matches records by:

- `bidgelyId`
- `start`
- `end`

It compares:

- sheet column `E` (`value_wh`)
- JSON `payload[].value`

It ignores the sheet `timestamp` column.

## Expected Sheet Columns

The sheet tab must contain these headers:

- `bidgelyId`
- `start`
- `end`
- `value_wh`

## Supported JSON Shape

Each line in each file should look like:

```json
{"request":{"bidgelyId":"..."},"payload":[{"start":1772928000,"end":1772928300,"value":29.55}]}
```

## Run

If the Google Sheet is private, export that tab as CSV first and use `--sheet-csv`.

```bash
python3 sheet_compare_project/compare_sheet_vs_s3.py \
  --sheet-csv "/path/to/bidgely_pv_sample.csv" \
  --s3-prefix-root "s3://your-bucket/path/to/files/" \
  --start-date "2026-03-07" \
  --end-date "2026-03-23" \
  --region "us-west-2" \
  --output-dir "sheet_compare_project/output"
```

For your partitioned layout, use:

```bash
python3 sheet_compare_project/compare_sheet_vs_s3.py \
  --sheet-csv "/path/to/bidgely_pv_sample.csv" \
  --s3-prefix-root "s3://bidgely-smud-itron-uat-external/solar_usage_data/duration=5min/" \
  --start-date "2026-03-07" \
  --end-date "2026-03-23" \
  --region "us-west-2" \
  --output-dir "sheet_compare_project/output"
```

## Output Files

- `sheet_only.csv`
- `json_only.csv`
- `value_mismatches.csv`
