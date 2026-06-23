import csv
import os
from config import OUTPUT_DIR
from utils.logger import get_logger

logger = get_logger("Report")


class ReportGenerator:

    def __init__(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def generate_hourly_report(self, dt, expected_users, hourly_data, queue_stats=None, missing_reason_details=None):
        date_dir = self._get_date_dir(dt)

        total_users = len(expected_users)

        if queue_stats is not None:
            logger.info(
                f"{dt} | Queue pending → visible: {queue_stats['visible']}, "
                f"in-flight: {queue_stats['inflight']}, total: {queue_stats['total']}"
            )
            self._write_queue_stats(date_dir, queue_stats)

        for hour in range(24):
            present_users = hourly_data.get(hour, set())
            missing_users = expected_users - present_users
            reason_counts = {}
            if missing_reason_details:
                reason_counts = {
                    reason: len(users)
                    for reason, users in missing_reason_details.get(hour, {}).items()
                    if users
                }

            logger.info(
                f"{dt} | Hour {hour:02d} → "
                f"Total: {total_users}, "
                f"Present: {len(present_users)}, "
                f"Missing: {len(missing_users)}"
            )
            if reason_counts:
                logger.info(
                    f"{dt} | Hour {hour:02d} missing reasons → "
                    + ", ".join(f"{reason}={count}" for reason, count in sorted(reason_counts.items()))
                )

            # 🔥 Write missing UUIDs per hour
            self._write_list(
                date_dir,
                f"hour_{hour:02d}_missing.txt",
                missing_users
            )

            # 🔥 (optional) write present users
            self._write_list(
                date_dir,
                f"hour_{hour:02d}_present.txt",
                present_users
            )

            for reason, users in (missing_reason_details or {}).get(hour, {}).items():
                if users:
                    self._write_list(
                        date_dir,
                        f"hour_{hour:02d}_{reason}.txt",
                        users,
                    )

    def _get_date_dir(self, dt):
        date_dir = os.path.join(OUTPUT_DIR, dt)
        os.makedirs(date_dir, exist_ok=True)
        return date_dir

    def _write_list(self, date_dir, filename, data):
        with open(os.path.join(date_dir, filename), "w") as f:
            for item in data:
                f.write(f"{item}\n")

    def _write_queue_stats(self, date_dir, stats):
        with open(os.path.join(date_dir, "queue_stats.txt"), "w") as f:
            f.write(f"visible_messages={stats['visible']}\n")
            f.write(f"inflight_messages={stats['inflight']}\n")
            f.write(f"total_messages={stats['total']}\n")

    def generate_hourly_summary_csv(self, dt, total_users, hourly_data, missing_reason_details=None):
        date_dir = self._get_date_dir(dt)
        csv_path = os.path.join(date_dir, "hourly_summary.csv")

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "hour",
                    "total_users",
                    "present_users",
                    "missing_users",
                    "weather_data_missing",
                    "weather_data_present_but_solar_missing",
                    "postal_missing",
                    "user_lookup_failed",
                ]
            )

            for hour in range(24):
                present_count = len(hourly_data.get(hour, set()))
                missing_count = max(0, total_users - present_count)
                reasons = (missing_reason_details or {}).get(hour, {})
                writer.writerow(
                    [
                        f"{hour:02d}",
                        total_users,
                        present_count,
                        missing_count,
                        len(reasons.get("weather_data_missing", [])),
                        len(reasons.get("weather_data_present_but_solar_missing", [])),
                        len(reasons.get("postal_missing", [])),
                        len(reasons.get("user_lookup_failed", [])),
                    ]
                )

        return csv_path
