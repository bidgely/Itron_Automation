from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def s3_client_for_region(region: str):
    import boto3

    session = boto3.session.Session(region_name=region)
    return session.client("s3")


def download_s3_file(s3_uri: str, destination: Path, region: str) -> Path:
    bucket, key = parse_s3_uri(s3_uri)
    destination.parent.mkdir(parents=True, exist_ok=True)
    client = s3_client_for_region(region)
    client.download_file(bucket, key, str(destination))
    return destination


def upload_s3_file(local_path: Path, bucket: str, key: str, region: str) -> str:
    client = s3_client_for_region(region)
    client.upload_file(str(local_path), bucket, key)
    return f"s3://{bucket}/{key}"


def build_archive_run_prefix(
    *,
    base_prefix: str,
    pilot_key: str,
    date_str: str,
    timestamp: str,
    request_name: str | None = None,
) -> str:
    prefix = base_prefix.strip("/")
    suffix = f"{timestamp}_{request_name}" if request_name else timestamp
    return f"{prefix}/results/{pilot_key}/{date_str}/{suffix}"


def delete_s3_prefix_older_than_days(bucket: str, prefix: str, region: str, *, days: int = 30) -> int:
    client = s3_client_for_region(region)
    paginator = client.get_paginator("list_objects_v2")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = 0
    keys_to_delete: list[dict[str, str]] = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for entry in page.get("Contents", []):
            if entry["LastModified"] < cutoff:
                keys_to_delete.append({"Key": entry["Key"]})
            if len(keys_to_delete) == 1000:
                client.delete_objects(Bucket=bucket, Delete={"Objects": keys_to_delete})
                deleted += len(keys_to_delete)
                keys_to_delete = []

    if keys_to_delete:
        client.delete_objects(Bucket=bucket, Delete={"Objects": keys_to_delete})
        deleted += len(keys_to_delete)

    return deleted
