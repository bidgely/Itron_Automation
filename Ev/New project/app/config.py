from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local dependency
    def load_dotenv() -> bool:
        return False


load_dotenv()


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class RedshiftConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class S3StageConfig:
    bucket: str
    prefix: str
    region: str
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None


def _env(*names: str, default: str | None = None) -> str:
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    if default is not None:
        return default
    joined = ", ".join(names)
    raise ValueError(f"Missing required environment variable. Checked: {joined}")


def get_mysql_config() -> MySQLConfig:
    return MySQLConfig(
        host=_env("ITRON_DB_HOST", "PILOT_10223_DB_HOST"),
        port=int(_env("ITRON_DB_PORT", "PILOT_10223_DB_PORT", default="3306")),
        user=_env("ITRON_DB_USER", "PILOT_10223_DB_USER"),
        password=_env("ITRON_DB_PASSWORD", "PILOT_10223_DB_PASSWORD"),
        database=_env("ITRON_DB_DATABASE", "PILOT_10223_DB_DATABASE"),
    )


def get_redshift_config() -> RedshiftConfig:
    return RedshiftConfig(
        host=_env("ITRON_RS_HOST", "REDSHIFT_HOST"),
        port=int(_env("ITRON_RS_PORT", "REDSHIFT_PORT", default="5439")),
        user=_env("ITRON_RS_USER", "REDSHIFT_USER"),
        password=_env("ITRON_RS_PASSWORD", "REDSHIFT_PASSWORD"),
        database=_env("ITRON_RS_DATABASE", "REDSHIFT_DATABASE", default="bdw"),
    )


def get_s3_stage_config() -> S3StageConfig:
    return S3StageConfig(
        bucket=_env("ITRON_S3_BUCKET", default="bidgely-artifacts2"),
        prefix=_env("ITRON_S3_PREFIX", default="Murali/itron-automation"),
        region=_env("AWS_REGION", "AWS_DEFAULT_REGION", default="us-west-2"),
        access_key_id=_env("AWS_ACCESS_KEY_ID"),
        secret_access_key=_env("AWS_SECRET_ACCESS_KEY"),
        session_token=os.getenv("AWS_SESSION_TOKEN"),
    )


def get_uat_mysql_config() -> MySQLConfig:
    return MySQLConfig(
        host=_env("ITRON_UAT_DB_HOST", "PILOT_10014_DB_HOST"),
        port=int(_env("ITRON_UAT_DB_PORT", "PILOT_10014_DB_PORT", default="3311")),
        user=_env("ITRON_UAT_DB_USER", "PILOT_10014_DB_USER"),
        password=_env("ITRON_UAT_DB_PASSWORD", "PILOT_10014_DB_PASSWORD"),
        database=_env("ITRON_UAT_DB_DATABASE", "PILOT_10014_DB_DATABASE"),
    )


def get_mysql_config_for_pilot(pilot_uat: bool) -> MySQLConfig:
    """Return UAT or prod MySQL config based on the pilot's environment."""
    if pilot_uat:
        return get_uat_mysql_config()
    return get_mysql_config()


def get_uat_redshift_config() -> RedshiftConfig:
    return RedshiftConfig(
        host=_env("WEATHER_REDSHIFT_HOST"),
        port=int(_env("WEATHER_REDSHIFT_PORT", default="5439")),
        user=_env("WEATHER_REDSHIFT_USER"),
        password=_env("WEATHER_REDSHIFT_PASSWORD"),
        database=_env("WEATHER_REDSHIFT_DATABASE", default="bdw"),
    )


def get_utils_repo_path(required: bool = False) -> Path | None:
    repo_path = os.getenv("ITRON_UTILS_REPO_PATH") or os.getenv("UTILS_REPO_PATH")
    if repo_path:
        return Path(repo_path)
    if required:
        raise ValueError(
            "Missing Utils repo path. Set --repo-path or export ITRON_UTILS_REPO_PATH/UTILS_REPO_PATH."
        )
    return None
