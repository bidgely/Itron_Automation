import json
import pymysql
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from config import DB_CONFIG
from utils.logger import get_logger

logger = get_logger("DBClient")


def _region_from_secret_arn(secret_arn: str):
    parts = (secret_arn or "").split(":")
    if len(parts) > 3 and parts[3]:
        return parts[3]
    return None


class DBClient:

    def __init__(
        self,
        pilot_id,
        secret_arn: str = None,
        db_config: dict = None,
        network_override: dict = None,
        aws_profile: str = None,
    ):
        """Create DB client. Priority:
        1. db_config (explicit dict)
        2. secret_arn (AWS Secrets Manager ARN)
        3. DB_CONFIG from config.py
        """
        self.pilot_id = pilot_id
        conn_kwargs = None

        if db_config:
            conn_kwargs = db_config
            logger.info("Using explicit DB config for pilot %s", pilot_id)
        elif secret_arn:
            # Attempt to retrieve secret from AWS Secrets Manager
            try:
                secret_region = _region_from_secret_arn(secret_arn)
                session = boto3.Session(profile_name=aws_profile) if aws_profile else boto3.Session()
                sm = session.client("secretsmanager", region_name=secret_region)
                resp = sm.get_secret_value(SecretId=secret_arn)
                secret_string = resp.get("SecretString")
                if secret_string:
                    data = json.loads(secret_string)
                else:
                    data = json.loads(resp.get("SecretBinary") or "{}")

                # Normalize keys
                host = data.get("host") or data.get("hostname") or data.get("Host")
                port = data.get("port") or data.get("Port")
                user = data.get("user") or data.get("username") or data.get("User")
                password = data.get("password") or data.get("Password")
                database = data.get("database") or data.get("dbname") or data.get("db")

                conn_kwargs = {
                    "host": host,
                    "port": int(port) if port else None,
                    "user": user,
                    "password": password,
                    "database": database,
                }
                logger.info("Loaded DB credentials from Secrets Manager for pilot %s", pilot_id)
            except (BotoCoreError, ClientError, ValueError, json.JSONDecodeError) as exc:
                logger.error("Failed to load DB secret %s: %s", secret_arn, exc)
                conn_kwargs = None

        if not conn_kwargs and DB_CONFIG:
            conn_kwargs = DB_CONFIG
            logger.info("Falling back to default DB_CONFIG for pilot %s", pilot_id)

        if not conn_kwargs:
            raise ValueError(
                f"No DB configuration available for pilot {pilot_id}. "
                "Configure a per-pilot secret ARN, per-pilot DB env vars, or shared DB env vars."
            )

        if network_override:
            conn_kwargs = conn_kwargs.copy()
            if network_override.get("host"):
                conn_kwargs["host"] = network_override["host"]
            if network_override.get("port"):
                conn_kwargs["port"] = network_override["port"]
            logger.info(
                "Applying DB network override for pilot %s -> %s:%s",
                pilot_id,
                conn_kwargs.get("host"),
                conn_kwargs.get("port"),
            )

        # Ensure pymysql receives expected parameter names (database vs db)
        # Some callers use 'database' key; pymysql accepts 'db' as alias, but passing as-is worked before.
        try:
            self.conn = pymysql.connect(**conn_kwargs)
        except Exception as exc:
            logger.error("Failed to connect to DB for pilot %s: %s", pilot_id, exc)
            raise

    def fetch_solar_users(self):
        query = f"""
            SELECT DISTINCT uuid
            FROM solar_users
            WHERE pilot_id = {self.pilot_id}
              AND solar = 1
        """

        logger.info("Fetching solar users from DB for pilot %s...", self.pilot_id)

        with self.conn.cursor() as cursor:
            cursor.execute(query)
            result = cursor.fetchall()

        users = set(row[0] for row in result)

        logger.info(f"Fetched {len(users)} users")
        return users

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
