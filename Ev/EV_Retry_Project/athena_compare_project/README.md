# Athena Compare Flow

This approach avoids opening hundreds of thousands of tiny S3 files from Python.

Instead, it:

1. reads the sheet CSV locally
2. uses Athena to query only the needed dates and UUIDs from S3
3. compares the Athena CSV result back to the sheet CSV locally

## Files

- `build_uuid_filter.py`
- `athena_query_template.sql`
- `compare_sheet_vs_athena_csv.py`

## Step 1: Build the UUID filter

Run:

```bash
python3 athena_compare_project/build_uuid_filter.py \
  --sheet-csv "/Users/saimuralidhar/Downloads/smud-data-analysis - bidgely_pv_sample.csv"
```

This prints:

- the unique UUID list
- an Athena-ready `IN (...)` clause you can paste into the SQL

## Step 2: Run Athena

Open [athena_query_template.sql](/Users/saimuralidhar/Documents/New%20project/athena_compare_project/athena_query_template.sql) and replace:

- `__UUID_FILTER__`
- `__DATABASE__`
- `__TABLE__`

Then run the query in Athena.

The query should export a CSV result with these columns:

- `bidgely_id`
- `start_epoch`
- `end_epoch`
- `json_value`

## Step 3: Compare Athena CSV vs sheet CSV

Run:

```bash
python3 athena_compare_project/compare_sheet_vs_athena_csv.py \
  --sheet-csv "/Users/saimuralidhar/Downloads/smud-data-analysis - bidgely_pv_sample.csv" \
  --athena-csv "/path/to/athena-result.csv" \
  --output-dir "athena_compare_project/output"
```

## Output Files

- `sheet_only.csv`
- `athena_only.csv`
- `value_mismatches.csv`
