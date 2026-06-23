from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_CONFIG_S3_URI = "s3://bidgely-artifacts2/Murali_Users/config/pilots.json"
DEFAULT_CACHE_PATH = Path("config_cache/pilots.latest.json")
DEFAULT_TTL_SECONDS = 60

_CACHE: dict | None = None
_CACHE_LOADED_AT: float = 0.0

_WEATHER_ENV_DEFAULTS = {
    "uat": {
        "lookup_mode": "redshift",
        "bucket": "bidgely-data-warehouse-uat",
        "prefix": "weather-data/weather-data-raw/v3/weather_data_type=FORECAST/duration=1h/country=US",
    },
    "prod": {
        "lookup_mode": "api",
        "bucket": "bidgely-data-warehouse-prod-na",
        "prefix": "weather-data/weather-data-raw/v3/weather_data_type=FORECAST/duration=1h/country=US",
    },
}


def _config_s3_uri() -> str:
    return os.getenv("PILOT_CONFIG_S3_URI", DEFAULT_CONFIG_S3_URI)


def _cache_path() -> Path:
    return Path(os.getenv("PILOT_CONFIG_CACHE_PATH", str(DEFAULT_CACHE_PATH)))


def _ttl() -> int:
    return int(os.getenv("PILOT_CONFIG_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))


def _cache_is_fresh() -> bool:
    if _CACHE is None:
        return False
    return (time.time() - _CACHE_LOADED_AT) < _ttl()


def _e(name: str, default=None):
    v = os.getenv(name)
    return v if v else default


def _ei(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v else default


def _pilot_db_config(pilot_id: int, db_name: str | None, env: str) -> dict | None:
    # Per-pilot override takes priority
    prefix = f"PILOT_{pilot_id}_DB"
    host = os.getenv(f"{prefix}_HOST")
    user = os.getenv(f"{prefix}_USER")
    password = os.getenv(f"{prefix}_PASSWORD")
    database = os.getenv(f"{prefix}_DATABASE") or db_name
    port_str = os.getenv(f"{prefix}_PORT")

    # Fall back to shared UAT/PROD credentials
    if not host:
        env_prefix = "UAT" if env == "uat" else "PROD"
        host = os.getenv(f"{env_prefix}_DB_HOST")
        user = user or os.getenv(f"{env_prefix}_DB_USER")
        password = password or os.getenv(f"{env_prefix}_DB_PASSWORD")
        port_str = port_str or os.getenv(f"{env_prefix}_DB_PORT")

    port = int(port_str) if port_str else 3306
    if not all([host, user, password, database]):
        return None
    return {"host": host, "port": port, "user": user, "password": password, "database": database}


def _pilot_db_tunnel(pilot_id: int) -> dict | None:
    prefix = f"PILOT_{pilot_id}_DB_TUNNEL"
    host = os.getenv(f"{prefix}_HOST")
    port = os.getenv(f"{prefix}_PORT")
    if not host and not port:
        return None
    return {"host": host, "port": int(port) if port else None}


def _read_s3(uri: str) -> bytes:
    import boto3
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    client = boto3.client("s3")
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def _save_cache(data: bytes) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _load_cache() -> bytes:
    path = _cache_path()
    if not path.exists():
        raise FileNotFoundError(f"No local backup found at {path}")
    return path.read_bytes()


def _parse(data: bytes) -> dict:
    payload = json.loads(data.decode("utf-8"))
    pilots = payload.get("pilots")
    if not isinstance(pilots, list) or not pilots:
        raise ValueError("pilots.json must have a non-empty 'pilots' list")

    PILOT_NAMES: dict = {}
    PILOT_S3_PREFIXES: dict = {}
    PILOT_EXPORT_S3: dict = {}
    PILOT_HOURLY_TRIGGER_THRESHOLDS: dict = {}
    PILOT_DB_SECRET_ARNS: dict = {}
    PILOT_DB_CONFIGS: dict = {}
    PILOT_DB_NETWORK_OVERRIDES: dict = {}
    PILOT_AWS_PROFILES: dict = {}
    PILOT_EXPORT_AWS_PROFILES: dict = {}
    PILOT_WEATHER_CONFIGS: dict = {}
    PILOT_REPORT_VARIANTS: dict = {}

    for p in pilots:
        pid = int(p["id"])
        env = str(p.get("env", "uat")).lower()
        wd = _WEATHER_ENV_DEFAULTS.get(env, _WEATHER_ENV_DEFAULTS["uat"])

        PILOT_NAMES[pid] = p["name"]

        PILOT_S3_PREFIXES[pid] = {
            "bucket": p["s3_bucket"],
            "base_prefix": p["s3_prefix"],
        }

        PILOT_EXPORT_S3[pid] = p.get("export_s3", "")

        PILOT_HOURLY_TRIGGER_THRESHOLDS[pid] = p.get("threshold", {"mode": "count", "value": 1})

        PILOT_DB_SECRET_ARNS[pid] = p.get("db_secret_arn")

        PILOT_DB_CONFIGS[pid] = _pilot_db_config(pid, p.get("db_name"), env)

        PILOT_DB_NETWORK_OVERRIDES[pid] = _pilot_db_tunnel(pid)

        aws_profile = _e(f"PILOT_{pid}_AWS_PROFILE")
        if not aws_profile and env == "prod":
            aws_profile = "tempna"
        PILOT_AWS_PROFILES[pid] = aws_profile
        PILOT_EXPORT_AWS_PROFILES[pid] = _e(f"PILOT_{pid}_EXPORT_AWS_PROFILE")

        PILOT_WEATHER_CONFIGS[pid] = {
            "lookup_mode": _e(f"PILOT_{pid}_WEATHER_LOOKUP_MODE", wd["lookup_mode"]),
            "api_base_url": _e(f"PILOT_{pid}_USER_API_BASE_URL", _e("USER_API_BASE_URL", "")),
            "token_env": _e(f"PILOT_{pid}_USER_API_TOKEN_ENV", _e("USER_API_TOKEN_ENV", "USER_API_BEARER_TOKEN")),
            "weather_bucket": _e(f"PILOT_{pid}_WEATHER_DATA_BUCKET", wd["bucket"]),
            "weather_prefix": _e(f"PILOT_{pid}_WEATHER_DATA_PREFIX", wd["prefix"]),
            "redshift_host": _e(f"PILOT_{pid}_WEATHER_REDSHIFT_HOST", _e("WEATHER_REDSHIFT_HOST")),
            "redshift_port": _ei(f"PILOT_{pid}_WEATHER_REDSHIFT_PORT", _ei("WEATHER_REDSHIFT_PORT", 5439)),
            "redshift_database": _e(f"PILOT_{pid}_WEATHER_REDSHIFT_DATABASE", _e("WEATHER_REDSHIFT_DATABASE", "bdw")),
            "redshift_user": _e(f"PILOT_{pid}_WEATHER_REDSHIFT_USER", _e("WEATHER_REDSHIFT_USER")),
            "redshift_password": _e(f"PILOT_{pid}_WEATHER_REDSHIFT_PASSWORD", _e("WEATHER_REDSHIFT_PASSWORD")),
            "redshift_query_template": _e(
                f"PILOT_{pid}_WEATHER_REDSHIFT_QUERY_TEMPLATE",
                _e("WEATHER_REDSHIFT_QUERY_TEMPLATE", "select distinct zip from home_meta_data where pilot_id = {pilot_id};"),
            ),
        }

        variants = p.get("variants")
        if variants:
            built = []
            for v in variants:
                entry: dict = {"name": v.get("name") or p["name"]}
                if "s3_bucket" in v or "s3_prefix" in v:
                    entry["s3"] = {
                        "bucket": v.get("s3_bucket", p["s3_bucket"]),
                        "base_prefix": v.get("s3_prefix", p["s3_prefix"]),
                    }
                if "export_s3" in v:
                    entry["export_s3"] = v["export_s3"]
                built.append(entry)
            PILOT_REPORT_VARIANTS[pid] = built

    return {
        "PILOT_NAMES": PILOT_NAMES,
        "PILOT_S3_PREFIXES": PILOT_S3_PREFIXES,
        "PILOT_EXPORT_S3": PILOT_EXPORT_S3,
        "PILOT_HOURLY_TRIGGER_THRESHOLDS": PILOT_HOURLY_TRIGGER_THRESHOLDS,
        "PILOT_DB_SECRET_ARNS": PILOT_DB_SECRET_ARNS,
        "PILOT_DB_CONFIGS": PILOT_DB_CONFIGS,
        "PILOT_DB_NETWORK_OVERRIDES": PILOT_DB_NETWORK_OVERRIDES,
        "PILOT_AWS_PROFILES": PILOT_AWS_PROFILES,
        "PILOT_EXPORT_AWS_PROFILES": PILOT_EXPORT_AWS_PROFILES,
        "PILOT_WEATHER_CONFIGS": PILOT_WEATHER_CONFIGS,
        "PILOT_REPORT_VARIANTS": PILOT_REPORT_VARIANTS,
    }


def load_pilot_configs(*, refresh: bool = False) -> dict:
    global _CACHE, _CACHE_LOADED_AT
    if not refresh and _cache_is_fresh():
        return _CACHE

    errors: list[str] = []

    try:
        data = _read_s3(_config_s3_uri())
        configs = _parse(data)
        _save_cache(data)
        _CACHE = configs
        _CACHE_LOADED_AT = time.time()
        return configs
    except Exception as exc:
        errors.append(f"S3: {exc}")

    try:
        data = _load_cache()
        configs = _parse(data)
        _CACHE = configs
        _CACHE_LOADED_AT = time.time()
        return configs
    except Exception as exc:
        errors.append(f"local backup: {exc}")

    raise RuntimeError("Failed to load pilot config. " + " | ".join(errors))
