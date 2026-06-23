import boto3
import json
from datetime import datetime
from config import (
    BUCKET,
    S3_CONNECT_TIMEOUT,
    S3_HOUR_READ_WORKERS,
    S3_MAX_POOL_CONNECTIONS,
    S3_MAX_RETRIES,
    S3_READ_TIMEOUT,
)
from utils.logger import get_logger
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.config import Config

logger = get_logger("S3Client")


class S3Client:

    def __init__(self, start_date, end_date, test_mode=False, bucket=None, session=None):
        # Tune connection pool, timeouts, and retries for many parallel S3 reads.
        boto_session = session or boto3.Session()
        self.s3 = boto_session.client(
            "s3",
            config=Config(
                max_pool_connections=S3_MAX_POOL_CONNECTIONS,
                connect_timeout=S3_CONNECT_TIMEOUT,
                read_timeout=S3_READ_TIMEOUT,
                retries={"max_attempts": S3_MAX_RETRIES, "mode": "adaptive"},
            ),
        )
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        self.test_mode = test_mode
        # Allow overriding the S3 bucket per client (per-pilot)
        self.bucket = bucket or BUCKET

    def list_prefixes(self, prefix):
        paginator = self.s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, Delimiter="/"):
            for p in page.get("CommonPrefixes", []):
                yield p["Prefix"]

    def is_valid_date(self, dt_str):
        dt = datetime.strptime(dt_str, "%Y-%m-%d")
        return self.start_date <= dt <= self.end_date

    def read_hour_data(self, hour_prefix):
        uuids = set()
        paginator = self.s3.get_paginator("list_objects_v2")

        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=hour_prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))

        # Cap worker count to avoid hammering S3 while still parallelizing IO
        worker_count = min(S3_HOUR_READ_WORKERS, max(1, len(keys)))

        def _load_keys(chunk):
            local = set()
            for key in chunk:
                try:
                    body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
                    for line in body.splitlines():
                        data = json.loads(line)
                        uuid = data.get("request", {}).get("bidgelyId")
                        if uuid:
                            local.add(uuid)
                except Exception as e:
                    logger.error(f"Error reading s3://{self.bucket}/{key}: {e}")
            return local

        # Split keys into roughly equal slices per worker
        if keys:
            chunk_size = max(1, len(keys) // worker_count)
            chunks = [keys[i:i + chunk_size] for i in range(0, len(keys), chunk_size)]

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(_load_keys, c) for c in chunks]
                for f in as_completed(futures):
                    uuids.update(f.result())

        return uuids
