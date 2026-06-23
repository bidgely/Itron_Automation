import os

from pilots.loader import load_pilot_configs as _load_pilot_configs


def _env_int(name, default):
    value = os.getenv(name)
    return int(value) if value else default


def _env_str(name, default=None):
    value = os.getenv(name)
    return value if value else default


def _db_config_from_env(prefix):
    host = os.getenv(f"{prefix}_HOST")
    user = os.getenv(f"{prefix}_USER")
    password = os.getenv(f"{prefix}_PASSWORD")
    database = os.getenv(f"{prefix}_DATABASE")
    port = _env_int(f"{prefix}_PORT", 3306)
    if not all([host, user, password, database]):
        return None
    return {"host": host, "port": port, "user": user, "password": password, "database": database}


DB_CONFIG = _db_config_from_env("DB")

BUCKET = "bidgely-smud-itron-uat-external"
BASE_PREFIX = "solar_usage_data/duration=5min/"

# --- Pilot configuration loaded from pilots.json (S3 → local backup fallback) ---
_cfg = _load_pilot_configs()

PILOT_NAMES = _cfg["PILOT_NAMES"]
PILOT_S3_PREFIXES = _cfg["PILOT_S3_PREFIXES"]
PILOT_EXPORT_S3 = _cfg["PILOT_EXPORT_S3"]
PILOT_REPORT_VARIANTS = _cfg["PILOT_REPORT_VARIANTS"]
PILOT_HOURLY_TRIGGER_THRESHOLDS = _cfg["PILOT_HOURLY_TRIGGER_THRESHOLDS"]
PILOT_DB_SECRET_ARNS = _cfg["PILOT_DB_SECRET_ARNS"]
PILOT_DB_CONFIGS = _cfg["PILOT_DB_CONFIGS"]
PILOT_DB_NETWORK_OVERRIDES = _cfg["PILOT_DB_NETWORK_OVERRIDES"]
PILOT_AWS_PROFILES = _cfg["PILOT_AWS_PROFILES"]
PILOT_EXPORT_AWS_PROFILES = _cfg["PILOT_EXPORT_AWS_PROFILES"]
PILOT_WEATHER_CONFIGS = _cfg["PILOT_WEATHER_CONFIGS"]

CHART_IMAGE_BUCKET = _env_str("CHART_IMAGE_BUCKET", "bidgely-email-images-nonprod")
CHART_IMAGE_PREFIX = _env_str("CHART_IMAGE_PREFIX", "Murali_Images")
CHART_CLOUDFRONT_BASE_URL = _env_str("CHART_CLOUDFRONT_BASE_URL", "https://d13hc4rsp6iv99.cloudfront.net")

OUTPUT_DIR = "output"

# SQS queue used for MiniDisagg processing status
SQS_QUEUE_URL = "https://sqs.us-west-2.amazonaws.com/189675173661/MiniDisaggGbTempDataReadyEventPyAmi-uat-itron"
SQS_REGION = "us-west-2"

# Google Chat notification settings (env-driven)
GCHAT_ENABLED = os.getenv("GCHAT_ENABLED", "false").lower() == "true"
GCHAT_WEBHOOK_URL = os.getenv("GCHAT_WEBHOOK_URL", "")
GCHAT_TIMEOUT_SECONDS = int(os.getenv("GCHAT_TIMEOUT_SECONDS", "10"))

# Missing-user report export target
MISSING_EXPORT_ENABLED = os.getenv("MISSING_EXPORT_ENABLED", "true").lower() == "true"
MISSING_EXPORT_S3_URI = os.getenv("MISSING_EXPORT_S3_URI", "s3://bidgely-artifacts2/Murali_Users/")

# User API + weather-data classification settings (used as global fallbacks in loader)
USER_API_BASE_URL = os.getenv("USER_API_BASE_URL", "https://api-server-itron-uat.bidgely.com/v2.0/users/")
USER_API_TOKEN_ENV = os.getenv("USER_API_TOKEN_ENV", "USER_API_BEARER_TOKEN")
WEATHER_DATA_BUCKET = os.getenv("WEATHER_DATA_BUCKET", "bidgely-data-warehouse-uat")
WEATHER_DATA_PREFIX = os.getenv(
    "WEATHER_DATA_PREFIX",
    "weather-data/weather-data-raw/v3/weather_data_type=FORECAST/duration=1h/country=US",
)
WEATHER_LOOKUP_MODE = os.getenv("WEATHER_LOOKUP_MODE", "api")
WEATHER_REDSHIFT_HOST = os.getenv("WEATHER_REDSHIFT_HOST")
WEATHER_REDSHIFT_PORT = _env_int("WEATHER_REDSHIFT_PORT", 5439)
WEATHER_REDSHIFT_DATABASE = os.getenv("WEATHER_REDSHIFT_DATABASE", "bdw")
WEATHER_REDSHIFT_USER = os.getenv("WEATHER_REDSHIFT_USER")
WEATHER_REDSHIFT_PASSWORD = os.getenv("WEATHER_REDSHIFT_PASSWORD")
WEATHER_REDSHIFT_QUERY_TEMPLATE = os.getenv(
    "WEATHER_REDSHIFT_QUERY_TEMPLATE",
    "select distinct zip from home_meta_data where pilot_id = {pilot_id};",
)

# Performance tuning knobs
S3_MAX_POOL_CONNECTIONS = _env_int("S3_MAX_POOL_CONNECTIONS", 64)
S3_CONNECT_TIMEOUT = _env_int("S3_CONNECT_TIMEOUT", 10)
S3_READ_TIMEOUT = _env_int("S3_READ_TIMEOUT", 60)
S3_MAX_RETRIES = _env_int("S3_MAX_RETRIES", 3)
S3_HOUR_READ_WORKERS = _env_int("S3_HOUR_READ_WORKERS", 16)
DATE_PROCESS_WORKERS = _env_int("DATE_PROCESS_WORKERS", 24)
WEATHER_S3_CHECK_WORKERS = _env_int("WEATHER_S3_CHECK_WORKERS", 32)
