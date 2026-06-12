import logging
import time

from src.core.config import AI_DISCOVERY_ENABLED, AI_DISCOVERY_INTERVAL_SECONDS, AI_SAMPLE_MIN_COUNT
from src.core.db import get_unanalyzed_sources
from src.core.ai_discovery import run_ai_analysis
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)


def run_ai_discovery_cycle():
    if not AI_DISCOVERY_ENABLED:
        return

    candidates = get_unanalyzed_sources(min_count=AI_SAMPLE_MIN_COUNT)
    if not candidates:
        log_info(logger, "[INFO] No AI discovery candidates found")
        return

    log_info(logger, f"[INFO] Found {len(candidates)} AI discovery candidates")

    for candidate in candidates:
        try:
            result = run_ai_analysis(
                source_ip=candidate["source_ip"],
                host=candidate["host"],
            )
            log_info(logger, f"[INFO] AI analysis result for {candidate['source_ip']}: {result.get('status')}")
        except Exception as e:
            log_error(logger, f"[ERROR] AI analysis failed for {candidate['source_ip']}: {e}")


if __name__ == "__main__":
    log_info(logger, "[INFO] AI discovery worker starting, waiting 30 seconds...")
    time.sleep(30)

    while True:
        try:
            if AI_DISCOVERY_ENABLED:
                run_ai_discovery_cycle()
            else:
                log_info(logger, "[INFO] AI discovery is disabled, sleeping...")
        except Exception as e:
            log_error(logger, f"[ERROR] AI worker error: {e}")

        time.sleep(AI_DISCOVERY_INTERVAL_SECONDS)
