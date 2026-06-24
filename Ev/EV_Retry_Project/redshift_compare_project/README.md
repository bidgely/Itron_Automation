# Redshift Compare Helpers

This folder contains standalone helpers for preparing compare files for Redshift.

## Prepare the sheet CSV for Redshift

```bash
python3 redshift_compare_project/prepare_sheet_stage_csv.py \
  --sheet-csv "/Users/saimuralidhar/Downloads/smud-data-analysis - bidgely_pv_sample.csv" \
  --output-csv "/Users/saimuralidhar/Documents/New project/redshift_compare_project/output/sheet_stage.csv"
```

The output columns are:

- `bidgely_id`
- `start_epoch`
- `end_epoch`
- `sheet_value`

## Prepare the JSON stage CSV from S3

```bash
/Users/saimuralidhar/Documents/New\ project/.venv/bin/python \
  redshift_compare_project/extract_json_stage_csv.py \
  --sheet-stage-csv "/Users/saimuralidhar/Documents/New project/redshift_compare_project/output/sheet_stage.csv" \
  --s3-prefix-root "s3://bidgely-smud-itron-uat-external/solar_usage_data/duration=5min/" \
  --region "us-west-2" \
  --output-csv "/Users/saimuralidhar/Documents/New project/redshift_compare_project/output/json_stage.csv" \
  --max-workers 12
```

The output columns are:

- `bidgely_id`
- `start_epoch`
- `end_epoch`
- `json_value`
