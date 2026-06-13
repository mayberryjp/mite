import hashlib
import logging
import re
import time

from src.core.db import (
    get_unprocessed_logs,
    insert_alert,
    mark_logs_processed,
    upsert_host,
    increment_host_alert_count,
    update_alert_discord_sent,
    get_pattern_by_id,
    get_patterns_with_regex,
    insert_pattern,
    increment_pattern_hit,
    increment_pattern_stat,
    update_pattern_classification,
)
from src.core.ai_discovery import classify_single_pattern
from src.core.discord import send_alert_discord
from src.core.config import DISCORD_WEBHOOK_URL
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

ALERT_SEVERITIES = {"critical", "high"}
MIN_MESSAGE_LENGTH = 20

# Cache of compiled regexes, refreshed periodically
_regex_cache = []
_regex_cache_time = 0
REGEX_CACHE_TTL = 60  # seconds


def _refresh_regex_cache():
    global _regex_cache, _regex_cache_time
    now = time.time()
    if now - _regex_cache_time < REGEX_CACHE_TTL and _regex_cache:
        return
    patterns = get_patterns_with_regex()
    compiled = []
    for p in patterns:
        try:
            compiled.append({
                "id": p["id"],
                "regex": re.compile(p["match_regex"]),
                "effective_classification": p["effective_classification"],
            })
        except re.error:
            pass
    _regex_cache = compiled
    _regex_cache_time = now


def _invalidate_regex_cache():
    global _regex_cache_time
    _regex_cache_time = 0


def match_by_regex(message):
    _refresh_regex_cache()
    for entry in _regex_cache:
        try:
            if entry["regex"].search(message):
                return entry["id"]
        except Exception:
            continue
    return None


def _hash_message(message):
    return hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest()[:16]


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

        message = log_entry.get("message", "")

        # Try matching against AI-provided regexes from known patterns
        regex_match_id = match_by_regex(message)

        if regex_match_id:
            # Matched an existing pattern via regex
            pattern_id = regex_match_id
            increment_pattern_hit(pattern_id, log_entry["received_at"])
            pattern = get_pattern_by_id(pattern_id)
        else:
            # No regex match — this is a new log type
            # Use a hash of the raw message as a unique key
            msg_hash = _hash_message(message)

            if len(message.strip()) < MIN_MESSAGE_LENGTH:
                # Too short to be meaningful — auto-classify as noise
                pattern_id = insert_pattern(
                    pattern_hash=msg_hash,
                    pattern_text=message,
                    sample_message=message,
                    host=log_entry.get("host"),
                    program=log_entry.get("program"),
                    timestamp=log_entry["received_at"],
                )
                update_pattern_classification(pattern_id, "noise", "Message too short to be meaningful.")
                pattern = {"id": pattern_id, "classification": "noise", "user_override": None, "ai_explanation": "Message too short to be meaningful."}
                log_info(logger, f"[INFO] Short message auto-classified as noise: {message[:60]}")
            else:
                # Insert pattern, then BLOCK and send to AI immediately
                pattern_id = insert_pattern(
                    pattern_hash=msg_hash,
                    pattern_text=message,
                    sample_message=message,
                    host=log_entry.get("host"),
                    program=log_entry.get("program"),
                    timestamp=log_entry["received_at"],
                )
                log_info(logger, f"[INFO] New log type — sending to AI for classification: {message[:80]}")

                ai_pattern = classify_single_pattern({
                    "id": pattern_id,
                    "pattern_text": message,
                    "sample_message": message,
                    "host": log_entry.get("host"),
                    "program": log_entry.get("program"),
                })

                if ai_pattern:
                    pattern = ai_pattern
                    _invalidate_regex_cache()
                    log_info(logger, f"[INFO] AI classified as '{pattern.get('classification')}' title='{pattern.get('title', '')}': {message[:80]}")
                else:
                    pattern = {"id": pattern_id, "classification": "pending", "user_override": None, "ai_explanation": None}
                    log_info(logger, f"[INFO] AI classification failed, pattern pending: {message[:80]}")

        # Record hourly stats for this pattern
        increment_pattern_stat(pattern_id, log_entry["received_at"])

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

            if DISCORD_WEBHOOK_URL and alert_id:
                success = send_alert_discord(
                    severity=effective,
                    pattern_text=pattern.get("title") or message[:80],
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
