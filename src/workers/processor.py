import logging
import time

from src.core.db import (
    get_unprocessed_logs,
    insert_alert,
    mark_logs_processed,
    upsert_host,
    increment_host_alert_count,
    update_alert_discord_sent,
    get_pattern_by_hash,
    insert_pattern,
    increment_pattern_hit,
)
from src.core.pattern_extractor import extract_pattern, hash_pattern
from src.core.discord import send_alert_discord
from src.core.config import DISCORD_WEBHOOK_URL
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

ALERT_SEVERITIES = {"critical", "high"}
MIN_MESSAGE_LENGTH = 20


def get_effective_classification(pattern):
    return pattern.get("user_override") or pattern.get("classification") or "pending"


def process_logs():
    logs = get_unprocessed_logs(limit=500)
    if not logs:
        return

    log_info(logger, f"[INFO] Processing {len(logs)} unprocessed logs")

    for log_entry in logs:
        # Track the host
        upsert_host(
            log_entry.get("host"),
            log_entry.get("source_ip"),
            log_entry["received_at"],
        )

        # Extract and look up pattern
        message = log_entry.get("message", "")
        pattern_text = extract_pattern(message)
        p_hash = hash_pattern(pattern_text)

        pattern = get_pattern_by_hash(p_hash)

        if pattern:
            # Known pattern — increment hit count
            pattern_id = pattern["id"]
            increment_pattern_hit(pattern_id, log_entry["received_at"])
        else:
            # New pattern — insert; classify short messages as noise automatically
            if len(message.strip()) < MIN_MESSAGE_LENGTH:
                pattern_id = insert_pattern(
                    pattern_hash=p_hash,
                    pattern_text=pattern_text,
                    sample_message=message,
                    host=log_entry.get("host"),
                    program=log_entry.get("program"),
                    timestamp=log_entry["received_at"],
                )
                from src.core.db import update_pattern_classification
                update_pattern_classification(pattern_id, "noise", "Message too short to be meaningful.")
                pattern = {"id": pattern_id, "classification": "noise", "user_override": None, "ai_explanation": "Message too short to be meaningful."}
            else:
                pattern_id = insert_pattern(
                    pattern_hash=p_hash,
                    pattern_text=pattern_text,
                    sample_message=message,
                    host=log_entry.get("host"),
                    program=log_entry.get("program"),
                    timestamp=log_entry["received_at"],
                )
                pattern = {"id": pattern_id, "classification": "pending", "user_override": None, "ai_explanation": None}
            log_info(logger, f"[INFO] New pattern discovered: {pattern_text[:80]}")

        # Check if this pattern is classified as important
        effective = get_effective_classification(pattern)

        if effective in ALERT_SEVERITIES:
            alert_id = insert_alert(
                created_at=log_entry["received_at"],
                log_id=log_entry["id"],
                pattern_id=pattern_id,
                severity=effective,
                host=log_entry.get("host"),
                source_ip=log_entry.get("source_ip"),
                message=message,
                reason=pattern.get("ai_explanation", ""),
                action="",
            )

            increment_host_alert_count(
                log_entry.get("host"), log_entry.get("source_ip")
            )

            # Send Discord notification for critical/high
            if DISCORD_WEBHOOK_URL and alert_id:
                success = send_alert_discord(
                    severity=effective,
                    pattern_text=pattern_text,
                    host=log_entry.get("host"),
                    source_ip=log_entry.get("source_ip"),
                    timestamp=log_entry["received_at"],
                    message=message,
                    ai_explanation=pattern.get("ai_explanation", ""),
                )
                if success:
                    update_alert_discord_sent(alert_id)

        # Mark log as processed with pattern link
        mark_logs_processed([log_entry["id"]], pattern_id=pattern_id)

    log_info(logger, f"[INFO] Processed {len(logs)} logs")


if __name__ == "__main__":
    log_info(logger, "[INFO] Log processor starting, waiting 10 seconds...")
    time.sleep(10)

    from src.core.db import init_database
    init_database()

    PROCESS_INTERVAL = 10

    while True:
        try:
            process_logs()
        except Exception as e:
            log_error(logger, f"[ERROR] Processor error: {e}")

        time.sleep(PROCESS_INTERVAL)
