from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


# Cache of assumed-role sessions keyed by role ARN, so a single cron run doesn't call
# sts:AssumeRole once per file upload. Re-assumed a few minutes before actual expiry.
_ASSUMED_SESSION_CACHE: dict[str, tuple[object, datetime]] = {}


def _assumed_role_session(role_arn: str, region: str, session_name: str = "itron-automation-nonprod"):
    """Return a boto3 Session backed by temporary credentials from sts:AssumeRole.

    The prod role's own identity-based policy only grants permissions inside the prod
    account, so writing to a bucket owned by the nonprod account (e.g. bidgely-artifacts2)
    requires assuming a role that lives in that nonprod account instead.
    """
    import boto3

    cached = _ASSUMED_SESSION_CACHE.get(role_arn)
    if cached is not None:
        session, expiration = cached
        if expiration - timedelta(minutes=5) > datetime.now(timezone.utc):
            return session

    sts_client = boto3.client("sts", region_name=region)
    response = sts_client.assume_role(RoleArn=role_arn, RoleSessionName=session_name)
    creds = response["Credentials"]
    session = boto3.session.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )
    _ASSUMED_SESSION_CACHE[role_arn] = (session, creds["Expiration"])
    return session


def s3_client_for_region(region: str, assume_role_arn: str | None = None):
    import boto3

    if assume_role_arn:
        session = _assumed_role_session(assume_role_arn, region)
    else:
        session = boto3.session.Session(region_name=region)
    return session.client("s3")


def download_s3_file(s3_uri: str, destination: Path, region: str) -> Path:
    bucket, key = parse_s3_uri(s3_uri)
    destination.parent.mkdir(parents=True, exist_ok=True)
    client = s3_client_for_region(region)
    client.download_file(bucket, key, str(destination))
    return destination


def upload_s3_file(
    local_path: Path,
    bucket: str,
    key: str,
    region: str,
    *,
    acl: str | None = None,
    assume_role_arn: str | None = None,
) -> str:
    client = s3_client_for_region(region, assume_role_arn=assume_role_arn)
    extra_args = {"ACL": acl} if acl else {}
    client.upload_file(str(local_path), bucket, key, ExtraArgs=extra_args)
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
