import logging
import time

from src.core.config import AI_DISCOVERY_ENABLED, AI_DISCOVERY_INTERVAL_SECONDS
from src.core.db import get_pending_patterns
from src.core.ai_discovery import classify_patterns
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

AI_BATCH_SIZE = 20


def run_ai_classification_cycle():
    """Retry classification for any patterns that failed inline classification."""
    if not AI_DISCOVERY_ENABLED:
        return

    pending = get_pending_patterns(limit=AI_BATCH_SIZE)
    if not pending:
        log_info(logger, "[INFO] No pending patterns to classify")
        return

    log_info(logger, f"[INFO] Classifying {len(pending)} pending patterns")

    result = classify_patterns(pending)
    log_info(logger, f"[INFO] AI classification result: {result}")


if __name__ == "__main__":
    log_info(logger, "[INFO] AI classification worker starting, waiting 30 seconds...")
    time.sleep(30)

    while True:
        try:
            if AI_DISCOVERY_ENABLED:
                run_ai_classification_cycle()
            else:
                log_info(logger, "[INFO] AI discovery is disabled, sleeping...")
        except Exception as e:
            log_error(logger, f"[ERROR] AI worker error: {e}")

        time.sleep(AI_DISCOVERY_INTERVAL_SECONDS)
