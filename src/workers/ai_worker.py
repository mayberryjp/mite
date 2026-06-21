import logging
import time

from src.core.ai_discovery import classify_patterns, review_pattern_regex_efficiency
from src.core.db import get_pending_patterns, get_setting, init_database, set_setting
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

AI_BATCH_SIZE_DEFAULT = 20
AI_DISCOVERY_INTERVAL_DEFAULT = 3600
AI_REGEX_REVIEW_INTERVAL_DEFAULT = 7 * 24 * 60 * 60


def _get_int_setting(key, default_value, min_value=1):
    raw_value = get_setting(key, str(default_value))
    try:
        parsed = int(raw_value)
        if parsed < min_value:
            raise ValueError(f"{key} must be >= {min_value}")
        return parsed
    except (TypeError, ValueError):
        log_error(
            logger,
            f"[ERROR] Invalid setting '{key}' value '{raw_value}', using default {default_value}",
        )
        return default_value


def run_ai_classification_cycle():
    """Retry classification for any patterns that failed inline classification."""
    ai_batch_size = _get_int_setting("ai_batch_size", AI_BATCH_SIZE_DEFAULT)
    pending = get_pending_patterns(limit=ai_batch_size)
    if not pending:
        log_info(logger, "[INFO] No pending patterns to classify")
        return

    log_info(logger, f"[INFO] Classifying {len(pending)} pending patterns")

    result = classify_patterns(pending)
    log_info(logger, f"[INFO] AI classification result: {result}")


def run_regex_review_cycle_if_due(force=False):
    review_interval = _get_int_setting(
        "ai_regex_review_interval_seconds", AI_REGEX_REVIEW_INTERVAL_DEFAULT
    )

    now_epoch = int(time.time())
    raw_last_run = get_setting("ai_regex_review_last_run_epoch", "0")
    try:
        last_run = int(raw_last_run)
    except (TypeError, ValueError):
        last_run = 0

    if not force and now_epoch - last_run < review_interval:
        return

    result = review_pattern_regex_efficiency()
    log_info(logger, f"[INFO] AI regex efficiency review result: {result}")

    # Record attempt time so this runs on a periodic cadence and avoids tight retry loops.
    set_setting("ai_regex_review_last_run_epoch", str(now_epoch))


if __name__ == "__main__":
    log_info(logger, "[INFO] AI classification worker starting, waiting 30 seconds...")
    time.sleep(30)
    init_database()

    # Force one startup pass so ai_efficiency_score is always refreshed.
    try:
        run_regex_review_cycle_if_due(force=True)
    except Exception as e:
        log_error(
            logger,
            f"[ERROR] Startup AI regex efficiency review failed: {type(e).__name__}: {e}",
        )

    while True:
        try:
            run_ai_classification_cycle()
            run_regex_review_cycle_if_due()
        except Exception as e:
            log_error(logger, f"[ERROR] AI worker error: {type(e).__name__}: {e}")

        sleep_seconds = _get_int_setting(
            "ai_discovery_interval_seconds", AI_DISCOVERY_INTERVAL_DEFAULT
        )
        time.sleep(sleep_seconds)
