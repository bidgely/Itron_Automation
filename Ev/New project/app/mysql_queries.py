from __future__ import annotations

FULL_METER_LIST_SQL = """
SELECT DISTINCT esn_list AS meterid
FROM meter_config_update_request
WHERE pilot_id = %s
  AND (feature IS NULL OR feature != 'HAS_SOLAR')
ORDER BY esn_list
"""


REQUEST_SENT_LIST_SQL = """
SELECT DISTINCT esn_list AS meterid
FROM meter_config_update_request
WHERE pilot_id = %s
  AND status = 'REQUEST_SENT'
ORDER BY esn_list
"""


CHECK_FOR_EV_ZERO_SQL = """
SELECT DISTINCT esn_list AS meterid
FROM meter_config_update_request
WHERE feature = 'HSM_EV'
  AND pilot_id = %s
  AND JSON_EXTRACT(configuration, '$.HSM_EV.CheckForEV') IS NOT NULL
  AND configuration->>'$.HSM_EV.CheckForEV' = '0'
  AND id > %s
ORDER BY esn_list
"""


LATEST_HSM_EV_CONFIG_SQL = """
WITH latest_rows AS (
    SELECT
        a.esn_list AS meterid,
        JSON_UNQUOTE(JSON_EXTRACT(a.configuration, '$.HSM_EV.meterId')) AS bidgelymeterid,
        JSON_UNQUOTE(JSON_EXTRACT(a.configuration, '$.HSM_EV.hsm_evconfig1')) AS evmin,
        JSON_UNQUOTE(JSON_EXTRACT(a.configuration, '$.HSM_EV.hsm_evparam1')) AS evmax,
        ROW_NUMBER() OVER (
            PARTITION BY a.esn_list
            ORDER BY a.updated_timestamp DESC
        ) AS rn
    FROM meter_config_update_request a
    WHERE a.pilot_id = %s
      AND a.feature = 'HSM_EV'
      AND a.configuration IS NOT NULL
)
SELECT meterid, bidgelymeterid, evmin, evmax
FROM latest_rows
WHERE rn = 1
  AND bidgelymeterid IS NOT NULL
  AND evmin IS NOT NULL
  AND evmax IS NOT NULL
ORDER BY meterid
"""
