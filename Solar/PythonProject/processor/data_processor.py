from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import DATE_PROCESS_WORKERS
from utils.logger import get_logger

logger = get_logger("Processor")


class DataProcessor:

    def __init__(self, s3_client):
        self.s3_client = s3_client

    def process_hour(self, hour_prefix):
        hour = int(hour_prefix.split("hour=")[-1].strip("/"))
        logger.info(f"Processing hour: {hour_prefix}")

        uuids = self.s3_client.read_hour_data(hour_prefix)

        local_map = {}
        for u in uuids:
            local_map[u] = (1 << hour)

        return hour, uuids, local_map   # 🔥 return hour + uuids also

    def process_date(self, date_prefix):
        dt = date_prefix.split("date=")[-1].strip("/")

        if not self.s3_client.is_valid_date(dt):
            return None, None, None

        logger.info(f"Processing date: {dt}")

        daily_data = {}  # uuid → bitmask
        hourly_data = defaultdict(set)  # 🔥 hour → uuids

        futures = []

        with ThreadPoolExecutor(max_workers=DATE_PROCESS_WORKERS) as executor:
            for hour_prefix in self.s3_client.list_prefixes(date_prefix):
                futures.append(executor.submit(self.process_hour, hour_prefix))

            for future in as_completed(futures):
                try:
                    hour, uuids, local_map = future.result()

                    # 🔥 hour-wise tracking
                    hourly_data[hour].update(uuids)

                    # 🔥 existing bitmask logic
                    for u, mask in local_map.items():
                        daily_data[u] = daily_data.get(u, 0) | mask

                except Exception as e:
                    logger.error(f"Thread error: {e}")

        return dt, daily_data, hourly_data
