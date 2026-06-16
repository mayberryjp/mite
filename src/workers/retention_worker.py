import logging
import time

from src.core.db import get_setting, init_database
from src.core.retention import run_retention
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

RETENTION_CHECK_INTERVAL_DEFAULT = 3600


def _get_retention_interval_seconds():
    raw_value = get_setting("retention_check_interval_seconds", str(RETENTION_CHECK_INTERVAL_DEFAULT))
    try:
        parsed = int(raw_value)
        if parsed < 1:
            raise ValueError("retention_check_interval_seconds must be >= 1")
        return parsed
    except (TypeError, ValueError):
        log_error(logger, f"[ERROR] Invalid setting 'retention_check_interval_seconds' value '{raw_value}', using default {RETENTION_CHECK_INTERVAL_DEFAULT}")
        return RETENTION_CHECK_INTERVAL_DEFAULT


if __name__ == "__main__":
    log_info(logger, "[INFO] Retention worker starting, waiting 30 seconds...")
    time.sleep(30)
    init_database()

    while True:
        try:
            run_retention()
        except Exception as e:
            log_error(logger, f"[ERROR] Retention worker error: {e}")

        time.sleep(_get_retention_interval_seconds())
