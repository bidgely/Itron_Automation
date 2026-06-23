WITH filtered_rows AS (
    SELECT
        request.bidgelyid AS bidgely_id,
        payload_item.start AS start_epoch,
        payload_item."end" AS end_epoch,
        CAST(payload_item.value AS DOUBLE) AS json_value
    FROM __DATABASE__.__TABLE__
    CROSS JOIN UNNEST(payload) AS t(payload_item)
    WHERE CAST("date" AS DATE) BETWEEN DATE '2026-03-07' AND DATE '2026-03-23'
      AND request.bidgelyid IN __UUID_FILTER__
)
SELECT
    bidgely_id,
    start_epoch,
    end_epoch,
    json_value
FROM filtered_rows
ORDER BY bidgely_id, start_epoch, end_epoch;

-- Expected source table shape:
-- request: struct<bidgelyid:string,userid:string>
-- payload: array<struct<appid:int,start:bigint,end:bigint,value:double>>
-- partitions or columns:
-- date string
-- hour string
-- minute string
