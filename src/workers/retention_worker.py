import logging
import time

from src.core.retention import run_retention
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

RETENTION_CHECK_INTERVAL = 3600  # Check every hour


if __name__ == "__main__":
    log_info(logger, "[INFO] Retention worker starting, waiting 30 seconds...")
    time.sleep(30)

    while True:
        try:
            run_retention()
        except Exception as e:
            log_error(logger, f"[ERROR] Retention worker error: {e}")

        time.sleep(RETENTION_CHECK_INTERVAL)
