from __future__ import annotations


def _matched_hsm_sql(full_table: str, latest_hsm_config_table: str, include_has: bool) -> str:
    ev_licence_cte = """,
    ev_licence_confirmed AS (
        SELECT DISTINCT meterid
        FROM public.itron_config_messages
        WHERE evlicence = 1
    )""" if include_has else ""

    ev_licence_join = """INNER JOIN ev_licence_confirmed elc
    ON a.meterid = elc.meterid
""" if include_has else ""

    return f"""
WITH latest_redshift_rows AS (
    SELECT
        a.meterid,
        a.bidgelymeterid,
        a.checkforev,
        a.evlicence,
        a.evmin,
        a.evmax,
        ROW_NUMBER() OVER (
            PARTITION BY a.meterid
            ORDER BY a.lastdatatimestamp DESC
        ) AS rn
    FROM public.itron_config_messages a
){ev_licence_cte}
SELECT DISTINCT cfg.meterid
FROM latest_redshift_rows a
INNER JOIN test_db.{latest_hsm_config_table} cfg
    ON a.bidgelymeterid = cfg.bidgelymeterid
INNER JOIN test_db.{full_table} b
    ON cfg.meterid = b.meterid
{ev_licence_join}WHERE a.rn = 1
  AND a.checkforev = 1
  AND CAST(a.evmin AS VARCHAR) = cfg.evmin
  AND CAST(a.evmax AS VARCHAR) = cfg.evmax
"""


def hsm_has_completed_sql(full_table: str, latest_hsm_config_table: str) -> str:
    return _matched_hsm_sql(full_table, latest_hsm_config_table, include_has=True)


def hsm_completed_sql(full_table: str, latest_hsm_config_table: str) -> str:
    return _matched_hsm_sql(full_table, latest_hsm_config_table, include_has=False)


def has_completed_sql(full_table: str, latest_hsm_config_table: str) -> str:
    """Meters where checkforev=1 (latest row) AND evlicence=1 (any time) — no evmin/evmax match required."""
    return f"""
WITH latest_redshift_rows AS (
    SELECT
        a.meterid,
        a.bidgelymeterid,
        a.checkforev,
        ROW_NUMBER() OVER (
            PARTITION BY a.meterid
            ORDER BY a.lastdatatimestamp DESC
        ) AS rn
    FROM public.itron_config_messages a
),
ev_licence_confirmed AS (
    SELECT DISTINCT meterid
    FROM public.itron_config_messages
    WHERE evlicence = 1
)
SELECT DISTINCT cfg.meterid
FROM latest_redshift_rows a
INNER JOIN test_db.{latest_hsm_config_table} cfg
    ON a.bidgelymeterid = cfg.bidgelymeterid
INNER JOIN test_db.{full_table} b
    ON cfg.meterid = b.meterid
INNER JOIN ev_licence_confirmed elc
    ON a.meterid = elc.meterid
WHERE a.rn = 1
  AND a.checkforev = 1
"""


def has_retry_sql(full_table: str, latest_hsm_config_table: str) -> str:
    return f"""
WITH
    matched_hsm_has AS (
        {hsm_has_completed_sql(full_table, latest_hsm_config_table)}
    ),
    matched_hsm AS (
        {hsm_completed_sql(full_table, latest_hsm_config_table)}
    )
SELECT DISTINCT h.meterid
FROM matched_hsm h
LEFT JOIN matched_hsm_has c
    ON h.meterid = c.meterid
WHERE c.meterid IS NULL
"""


def hsm_retry_sql(full_table: str, checkforev_zero_table: str, latest_hsm_config_table: str) -> str:
    return f"""
WITH
    matched_hsm AS (
        {hsm_completed_sql(full_table, latest_hsm_config_table)}
    )
SELECT DISTINCT meterid
FROM (
    SELECT meterid
    FROM test_db.{full_table}
    EXCEPT
    SELECT meterid
    FROM test_db.{checkforev_zero_table}
    EXCEPT
    SELECT meterid
    FROM matched_hsm
) t
"""


def leftovers_sql(full_table: str, checkforev_zero_table: str) -> str:
    return f"""
WITH hsm_has_ev AS (
    SELECT DISTINCT a.meterid
    FROM public.itron_config_messages a
    INNER JOIN test_db.{full_table} b
        ON a.meterid = b.meterid
    WHERE a.checkforev = 1
       OR a.evlicence = 1
)
SELECT a.meterid
FROM test_db.{full_table} a
LEFT JOIN hsm_has_ev b
    ON a.meterid = b.meterid
LEFT JOIN test_db.{checkforev_zero_table} c
    ON a.meterid = c.meterid
WHERE b.meterid IS NULL
  AND c.meterid IS NULL
"""
