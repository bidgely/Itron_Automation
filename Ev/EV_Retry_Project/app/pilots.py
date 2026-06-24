from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .s3_utils import parse_s3_uri, s3_client_for_region


DEFAULT_PILOT_CONFIG_S3_URI = "s3://bidgely-artifacts2/Murali/itron-automation/config/pilots.json"
DEFAULT_PILOT_CONFIG_CACHE_PATH = Path("config_cache/pilots.latest.json")
DEFAULT_PILOT_CONFIG_REFRESH_SECONDS = 60


@dataclass(frozen=True)
class PilotDefinition:
    key: str
    pilot_id: int
    display_name: str
    uat: bool = False
    checkforev_zero_min_id: int = 995
    redshift_full_table: str | None = None
    redshift_request_sent_table: str | None = None
    redshift_checkforev_zero_table: str | None = None
    special_request_s3_prefix: str | None = None


_PILOT_CACHE: dict[str, PilotDefinition] | None = None
_PILOT_CACHE_LOADED_AT = 0.0


def _pilot_config_s3_uri() -> str:
    return os.getenv("ITRON_PILOT_CONFIG_S3_URI", DEFAULT_PILOT_CONFIG_S3_URI)


def _pilot_config_cache_path() -> Path:
    return Path(os.getenv("ITRON_PILOT_CONFIG_CACHE_PATH", str(DEFAULT_PILOT_CONFIG_CACHE_PATH)))


def _aws_region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-west-2"


def _pilot_config_refresh_seconds() -> int:
    return int(os.getenv("ITRON_PILOT_CONFIG_REFRESH_SECONDS", str(DEFAULT_PILOT_CONFIG_REFRESH_SECONDS)))


def _pilot_cache_is_fresh() -> bool:
    if _PILOT_CACHE is None:
        return False
    return (time.time() - _PILOT_CACHE_LOADED_AT) < _pilot_config_refresh_seconds()


def _normalize_pilot(raw: dict) -> PilotDefinition:
    key = str(raw.get("key", "")).strip().lower()
    display_name = str(raw.get("display_name") or key.upper()).strip()
    if not key:
        raise ValueError("Pilot config entry is missing key")
    if "pilot_id" not in raw:
        raise ValueError(f"Pilot config entry '{key}' is missing pilot_id")

    environment = str(raw.get("environment", "")).strip().lower()
    return PilotDefinition(
        key=key,
        pilot_id=int(raw["pilot_id"]),
        display_name=display_name,
        uat=bool(raw.get("uat", False)) or environment == "uat",
        checkforev_zero_min_id=int(raw.get("checkforev_zero_min_id", 995)),
        redshift_full_table=raw.get("redshift_full_table"),
        redshift_request_sent_table=raw.get("redshift_request_sent_table"),
        redshift_checkforev_zero_table=raw.get("redshift_checkforev_zero_table"),
        special_request_s3_prefix=raw.get("special_request_s3_prefix"),
    )


def _parse_config_bytes(config_bytes: bytes) -> dict[str, PilotDefinition]:
    payload = json.loads(config_bytes.decode("utf-8"))
    pilots = payload.get("pilots")
    if not isinstance(pilots, list) or not pilots:
        raise ValueError("Pilot config must contain a non-empty 'pilots' list")

    definitions = [_normalize_pilot(entry) for entry in pilots if isinstance(entry, dict)]
    if not definitions:
        raise ValueError("Pilot config did not contain any valid pilot entries")

    result: dict[str, PilotDefinition] = {}
    for pilot in definitions:
        if pilot.key in result:
            raise ValueError(f"Duplicate pilot key in config: {pilot.key}")
        result[pilot.key] = pilot
    return result


def _read_s3_object(s3_uri: str) -> bytes:
    bucket, key = parse_s3_uri(s3_uri)
    client = s3_client_for_region(_aws_region())
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def _read_latest_s3_config_under_prefix(s3_uri: str) -> bytes:
    bucket, key = parse_s3_uri(s3_uri)
    prefix = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
    client = s3_client_for_region(_aws_region())
    paginator = client.get_paginator("list_objects_v2")
    latest_object = None

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for entry in page.get("Contents", []):
            if not entry["Key"].endswith(".json"):
                continue
            if latest_object is None or entry["LastModified"] > latest_object["LastModified"]:
                latest_object = entry

    if latest_object is None:
        raise FileNotFoundError(f"No pilot config JSON files found under s3://{bucket}/{prefix}")
    return client.get_object(Bucket=bucket, Key=latest_object["Key"])["Body"].read()


def _load_from_local_cache() -> dict[str, PilotDefinition]:
    cache_path = _pilot_config_cache_path()
    if not cache_path.exists():
        raise FileNotFoundError(f"Pilot config cache not found: {cache_path}")
    return _parse_config_bytes(cache_path.read_bytes())


def _save_local_cache(config_bytes: bytes) -> None:
    cache_path = _pilot_config_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(config_bytes)


def load_pilot_definitions(*, refresh: bool = False) -> dict[str, PilotDefinition]:
    global _PILOT_CACHE, _PILOT_CACHE_LOADED_AT
    if not refresh and _pilot_cache_is_fresh():
        return _PILOT_CACHE

    s3_uri = _pilot_config_s3_uri()
    errors: list[str] = []

    for loader_name, loader in (
        ("configured S3 pilot config", lambda: _read_s3_object(s3_uri)),
        ("latest S3 pilot config in folder", lambda: _read_latest_s3_config_under_prefix(s3_uri)),
    ):
        try:
            config_bytes = loader()
            definitions = _parse_config_bytes(config_bytes)
            _save_local_cache(config_bytes)
            _PILOT_CACHE = definitions
            _PILOT_CACHE_LOADED_AT = time.time()
            return definitions
        except Exception as exc:
            errors.append(f"{loader_name}: {exc}")

    try:
        definitions = _load_from_local_cache()
        _PILOT_CACHE = definitions
        _PILOT_CACHE_LOADED_AT = time.time()
        return definitions
    except Exception as exc:
        errors.append(f"local cached pilot config: {exc}")

    raise RuntimeError("Unable to load pilot config. " + " | ".join(errors))


def get_all_pilot_definitions(*, refresh: bool = False) -> dict[str, PilotDefinition]:
    return load_pilot_definitions(refresh=refresh)


def get_supported_pilot_keys(*, refresh: bool = False) -> list[str]:
    return sorted(load_pilot_definitions(refresh=refresh))


def get_pilot_definition(pilot_key: str) -> PilotDefinition:
    normalized_key = pilot_key.lower().strip()
    pilots = load_pilot_definitions()
    if normalized_key not in pilots:
        supported = ", ".join(sorted(pilots))
        raise ValueError(f"Unsupported pilot '{pilot_key}'. Supported pilots: {supported}")
    return pilots[normalized_key]
