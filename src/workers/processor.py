import hashlib
import logging
import re
import sys
import time

from src.core.db import (
    get_unprocessed_logs,
    insert_alert,
    mark_logs_processed,
    delete_logs,
    upsert_host,
    increment_host_alert_count,
    update_alert_discord_sent,
    get_pattern_by_hash,
    get_pattern_by_id,
    get_patterns_with_regex,
    insert_pattern,
    increment_pattern_hit,
    increment_pattern_stat,
    increment_noise_stat,
    get_setting,
    update_pattern_classification,
)
from src.core.ai_discovery import classify_single_pattern, test_ai_connection
from src.core.discord import send_alert_discord
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

ALERT_SEVERITIES = {"critical"}
MIN_MESSAGE_LENGTH_DEFAULT = 50
MIN_MESSAGE_LENGTH = MIN_MESSAGE_LENGTH_DEFAULT
PROCESS_INTERVAL_DEFAULT = 10
PROCESS_INTERVAL = PROCESS_INTERVAL_DEFAULT
PROCESS_FETCH_LIMIT_DEFAULT = 100
PROCESS_FETCH_LIMIT = PROCESS_FETCH_LIMIT_DEFAULT
REGEX_CACHE_TTL_DEFAULT = 60


def _load_min_message_length_setting():
    """Load minimum message length from settings table with a safe fallback."""
    global MIN_MESSAGE_LENGTH
    raw_value = get_setting("min_message_length", str(MIN_MESSAGE_LENGTH_DEFAULT))
    try:
        value = int(raw_value)
        if value < 0:
            raise ValueError("min_message_length must be non-negative")
        MIN_MESSAGE_LENGTH = value
    except (TypeError, ValueError):
        MIN_MESSAGE_LENGTH = MIN_MESSAGE_LENGTH_DEFAULT
        log_error(
            logger,
            f"[ERROR] Invalid min_message_length setting '{raw_value}', using default {MIN_MESSAGE_LENGTH_DEFAULT}",
        )


def _is_meaningful_message(message):
    """Check if a message has enough real content to be worth classifying."""
    # Strip the message
    msg = message.strip()
    if len(msg) < MIN_MESSAGE_LENGTH:
        return False
    # Remove timestamps, IPs, dashes, colons, plus signs, and whitespace
    # to see if there's any real content left
    stripped = re.sub(r'[\d:.+\-/T\s]+', ' ', msg).strip()
    # Count actual alphabetic words (not single chars)
    words = [w for w in stripped.split() if len(w) > 1 and any(c.isalpha() for c in w)]
    return len(words) >= 3

# Cache of compiled regexes, refreshed periodically
_regex_cache = []
_regex_cache_time = 0
REGEX_CACHE_TTL = REGEX_CACHE_TTL_DEFAULT


def _load_runtime_settings():
    """Load processor runtime tuning settings from DB with safe fallbacks."""
    global PROCESS_INTERVAL, PROCESS_FETCH_LIMIT, REGEX_CACHE_TTL

    runtime_keys = (
        ("processor_interval_seconds", PROCESS_INTERVAL_DEFAULT, "PROCESS_INTERVAL"),
        ("processor_fetch_limit", PROCESS_FETCH_LIMIT_DEFAULT, "PROCESS_FETCH_LIMIT"),
        ("regex_cache_ttl_seconds", REGEX_CACHE_TTL_DEFAULT, "REGEX_CACHE_TTL"),
    )

    for key, default_value, attr_name in runtime_keys:
        raw_value = get_setting(key, str(default_value))
        try:
            parsed = int(raw_value)
            if parsed < 1:
                raise ValueError(f"{key} must be >= 1")
            globals()[attr_name] = parsed
        except (TypeError, ValueError):
            globals()[attr_name] = default_value
            log_error(logger, f"[ERROR] Invalid setting '{key}' value '{raw_value}', using default {default_value}")


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
    """Returns (pattern_id, effective_classification) or (None, None)."""
    _refresh_regex_cache()
    for entry in _regex_cache:
        try:
            if entry["regex"].search(message):
                return entry["id"], entry["effective_classification"]
        except Exception:
            continue
    return None, None


def _hash_message(message):
    return hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest()[:16]


def get_effective_classification(pattern):
    return pattern.get("user_override") or pattern.get("classification") or "pending"


def process_log(log_entry):
    """Process a single log entry. Returns False if processing should stop (AI failure on new pattern)."""

    # Track the host
    upsert_host(
        log_entry.get("host"),
        log_entry.get("source_ip"),
        log_entry["received_at"],
    )

    message = log_entry.get("message", "")

    # Step 1: Try matching against AI-provided regexes from known patterns
    regex_match_id, regex_classification = match_by_regex(message)

    if regex_match_id:
        # Matched an existing pattern via regex
        pattern_id = regex_match_id
        increment_pattern_hit(pattern_id, log_entry["received_at"])
        if regex_classification == "noise":
            increment_pattern_stat(pattern_id, log_entry["received_at"])
            increment_noise_stat(log_entry["received_at"])
            delete_logs([log_entry["id"]])
            return True
        pattern = get_pattern_by_id(pattern_id)
    else:
        # Step 2: Check if this exact message hash already exists
        msg_hash = _hash_message(message)
        existing = get_pattern_by_hash(msg_hash)

        if existing:
            pattern_id = existing["id"]
            increment_pattern_hit(pattern_id, log_entry["received_at"])
            effective_existing = get_effective_classification(existing)
            if effective_existing == "noise":
                increment_pattern_stat(pattern_id, log_entry["received_at"])
                increment_noise_stat(log_entry["received_at"])
                delete_logs([log_entry["id"]])
                return True
            pattern = existing
        elif not _is_meaningful_message(message):
            # Not enough real content — silently drop
            delete_logs([log_entry["id"]])
            return True
        else:
            # Step 3: New pattern — insert then BLOCK and send to AI
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
                # AI failed — mark this log processed but STOP processing more logs
                log_error(logger, f"[ERROR] AI classification failed — stopping processing until next cycle")
                mark_logs_processed([log_entry["id"]], pattern_id=pattern_id)
                increment_pattern_stat(pattern_id, log_entry["received_at"])
                return False

    # Record hourly stats for this pattern
    increment_pattern_stat(pattern_id, log_entry["received_at"])

    # Check effective classification — silently drop noise logs
    effective = get_effective_classification(pattern)

    if effective == "noise":
        increment_noise_stat(log_entry["received_at"])
        delete_logs([log_entry["id"]])
        return True

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

        if alert_id and effective == "critical":
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
    return True


def process_logs():
    _load_runtime_settings()
    logs = get_unprocessed_logs(limit=PROCESS_FETCH_LIMIT)
    if not logs:
        return

    log_info(logger, f"[INFO] Processing {len(logs)} unprocessed logs")

    for log_entry in logs:
        try:
            should_continue = process_log(log_entry)
            if not should_continue:
                log_error(logger, "[ERROR] Stopping log processing — AI unavailable. Will retry next cycle.")
                return
        except Exception as e:
            log_error(logger, f"[ERROR] Error processing log {log_entry.get('id')}: {type(e).__name__}: {e}")
            # Don't mark as processed — retry next cycle
            return

    log_info(logger, f"[INFO] Processed {len(logs)} logs")


if __name__ == "__main__":
    log_info(logger, "[INFO] Log processor starting, waiting 10 seconds...")
    time.sleep(10)

    from src.core.db import init_database
    init_database()
    _load_min_message_length_setting()
    _load_runtime_settings()
    log_info(logger, f"[INFO] min_message_length set to {MIN_MESSAGE_LENGTH}")

    # Test AI connectivity at startup — fail hard if not configured
    log_info(logger, "[INFO] Testing AI API connectivity...")
    success, error = test_ai_connection()
    if not success:
        log_error(logger, f"[FATAL] AI API is not available: {error}")
        log_error(logger, "[FATAL] Processor cannot start without a working AI connection. Fix AI_API_BASE_URL, AI_API_KEY, and AI_MODEL then restart.")
        sys.exit(1)
    log_info(logger, "[INFO] AI API connection successful")

    while True:
        try:
            process_logs()
        except Exception as e:
            log_error(logger, f"[ERROR] Processor error: {type(e).__name__}: {e}")

        time.sleep(PROCESS_INTERVAL)
