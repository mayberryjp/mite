import logging
import re
import sys
import time

from src.core.ai_discovery import (
    classify_single_pattern,
    preprocess_sample_for_ai,
    test_ai_connection,
)
from src.core.db import (
    delete_logs,
    get_pattern_by_hash,
    get_pattern_by_id,
    get_patterns_with_regex,
    get_setting,
    get_unprocessed_logs,
    increment_discarded_too_small_count,
    increment_host_alert_count,
    increment_noise_stat,
    increment_pattern_hit,
    increment_pattern_stat,
    insert_alert,
    insert_pattern,
    mark_logs_processed,
    update_alert_discord_sent,
    upsert_host,
)
from src.core.discord import send_alert_discord
from src.core.pattern_extractor import extract_pattern, hash_pattern
from src.utils.locallogging import log_error, log_info, write_syslog_daily_log

logger = logging.getLogger(__name__)

ALERT_SEVERITIES = {"critical"}
MIN_MESSAGE_LENGTH_DEFAULT = 50
MIN_MESSAGE_LENGTH = MIN_MESSAGE_LENGTH_DEFAULT
PROCESS_INTERVAL_DEFAULT = 10
PROCESS_INTERVAL = PROCESS_INTERVAL_DEFAULT
PROCESS_FETCH_LIMIT_DEFAULT = 100
PROCESS_FETCH_LIMIT = PROCESS_FETCH_LIMIT_DEFAULT
REGEX_CACHE_TTL_DEFAULT = 60
MAX_AI_REGEX_ATTEMPTS = 3


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


def _is_meaningful_message(tokenized_message):
    """Check if a message has enough real content to be worth classifying.

    Minimum length is evaluated on the preprocessed sample so dynamic numbers/symbols
    do not inflate a low-signal log into passing the threshold.
    """
    preprocessed = (tokenized_message or "").strip()
    if len(preprocessed) < MIN_MESSAGE_LENGTH:
        return False

    # Remove placeholder tokens and punctuation to estimate remaining keyword signal.
    stripped = re.sub(r"<X>|[^A-Za-z\s]", " ", preprocessed).strip()
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

    def _read_runtime_int(key, default_value):
        raw_value = get_setting(key, str(default_value))
        try:
            parsed = int(raw_value)
            if parsed < 1:
                raise ValueError(f"{key} must be >= 1")
            return parsed
        except (TypeError, ValueError):
            log_error(
                logger,
                f"[ERROR] Invalid setting '{key}' value '{raw_value}', using default {default_value}",
            )
            return default_value

    PROCESS_INTERVAL = _read_runtime_int(
        "processor_interval_seconds", PROCESS_INTERVAL_DEFAULT
    )
    PROCESS_FETCH_LIMIT = _read_runtime_int(
        "processor_fetch_limit", PROCESS_FETCH_LIMIT_DEFAULT
    )
    REGEX_CACHE_TTL = _read_runtime_int(
        "regex_cache_ttl_seconds", REGEX_CACHE_TTL_DEFAULT
    )


def _refresh_regex_cache():
    global _regex_cache, _regex_cache_time
    now = time.time()
    if now - _regex_cache_time < REGEX_CACHE_TTL and _regex_cache:
        return
    patterns = get_patterns_with_regex()
    compiled = []
    for p in patterns:
        try:
            compiled.append(
                {
                    "id": p["id"],
                    "regex": re.compile(p["match_regex"]),
                    "effective_classification": p["effective_classification"],
                }
            )
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


def get_effective_classification(pattern):
    return pattern.get("user_override") or pattern.get("classification") or "pending"


def _truncate_for_log(text, limit=500):
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _pattern_regex_matches_message(pattern, message):
    """Return True when a pattern's regex is present and matches the current log message."""
    if not pattern:
        return False

    match_regex = pattern.get("match_regex")
    if not match_regex:
        return False

    try:
        return re.search(match_regex, message) is not None
    except re.error as e:
        log_error(
            logger,
            f"[ERROR] Stored regex is invalid for pattern {pattern.get('id')}: {e}",
        )
        return False


def _classify_until_regex_matches(
    pattern_id, normalized_pattern, tokenized_message, host=None, program=None
):
    """Classify with AI and require regex to match the originating log before accepting."""
    debug_preprocessed_message = _truncate_for_log(tokenized_message)
    log_error(
        logger,
        f"[DEBUG] Pattern {pattern_id} tokenized message sent to AI: {debug_preprocessed_message!r}",
    )
    previous_regex = None

    for attempt in range(1, MAX_AI_REGEX_ATTEMPTS + 1):
        retry_feedback = None
        if previous_regex:
            retry_feedback = (
                "Previous attempt failed. The generated regex did not match the tokenized log. "
                "Create a NEW regex (not a minor rewording) focused on stable keywords and delimiters, "
                "with bounded wildcards for variable segments. "
                f"Failed regex: {previous_regex!r}. "
                f"Tokenized log: {tokenized_message!r}"
            )
            log_error(
                logger,
                f"[DEBUG] Pattern {pattern_id} retry feedback sent to AI (attempt {attempt}/{MAX_AI_REGEX_ATTEMPTS})",
            )

        ai_pattern = classify_single_pattern(
            {
                "id": pattern_id,
                "pattern_text": normalized_pattern,
                "sample_message": tokenized_message,
                "sample_is_preprocessed": True,
                "host": host,
                "program": program,
                "retry_feedback": retry_feedback,
            }
        )

        if not ai_pattern:
            log_error(
                logger,
                f"[ERROR] AI classification returned no result for pattern {pattern_id} (attempt {attempt}/{MAX_AI_REGEX_ATTEMPTS})",
            )
            continue

        if _pattern_regex_matches_message(ai_pattern, tokenized_message):
            accepted_regex = _truncate_for_log(
                (ai_pattern.get("match_regex") or "").strip()
            )
            log_info(
                logger,
                f"[INFO] Pattern {pattern_id} AI regex accepted (attempt {attempt}/{MAX_AI_REGEX_ATTEMPTS}): {accepted_regex!r}",
            )
            _invalidate_regex_cache()
            # Refresh immediately so the very next log uses the new regex.
            _refresh_regex_cache()
            return ai_pattern

        debug_regex = _truncate_for_log((ai_pattern.get("match_regex") or "").strip())
        debug_message = _truncate_for_log(tokenized_message)
        previous_regex = ai_pattern.get("match_regex") or ""
        log_error(
            logger,
            f"[ERROR] AI regex did not match source log for pattern {pattern_id} (attempt {attempt}/{MAX_AI_REGEX_ATTEMPTS}); retrying",
        )
        log_error(logger, f"[DEBUG] Pattern {pattern_id} regex: {debug_regex!r}")
        log_error(logger, f"[DEBUG] Pattern {pattern_id} message: {debug_message!r}")

    return None


def process_log(log_entry):
    """Process a single log entry. Returns True to continue processing."""

    # Track the host
    upsert_host(
        log_entry.get("host"),
        log_entry.get("source_ip"),
        log_entry["received_at"],
    )

    message = log_entry.get("message", "")
    tokenized_message = preprocess_sample_for_ai(message)
    normalized_pattern = extract_pattern(tokenized_message)

    # Step 1: Try matching against AI-provided regexes from known patterns
    regex_match_id, regex_classification = match_by_regex(tokenized_message)
    pattern = None
    pattern_id = None

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
        if not pattern:
            log_error(
                logger,
                f"[ERROR] Regex matched missing pattern id={pattern_id}; dropping log {log_entry['id']}",
            )
            _invalidate_regex_cache()
            delete_logs([log_entry["id"]])
            return True
    else:
        if not _is_meaningful_message(tokenized_message):
            # Not enough real content — silently drop
            increment_discarded_too_small_count()
            delete_logs([log_entry["id"]])
            return True

        # Deterministic pattern identity prevents duplicate patterns for equivalent logs.
        pattern_hash = hash_pattern(normalized_pattern)
        pattern = get_pattern_by_hash(pattern_hash)

        if pattern:
            pattern_id = pattern["id"]
            increment_pattern_hit(pattern_id, log_entry["received_at"])
            log_info(
                logger,
                f"[INFO] Reusing existing pattern by hash {pattern_hash} (id={pattern_id})",
            )
        else:
            pattern_id = insert_pattern(
                pattern_hash=pattern_hash,
                pattern_text=normalized_pattern,
                sample_message=tokenized_message,
                host=log_entry.get("host"),
                program=log_entry.get("program"),
                timestamp=log_entry["received_at"],
            )
            pattern = get_pattern_by_id(pattern_id)

        if not pattern:
            log_error(
                logger,
                f"[ERROR] Pattern lookup failed for log {log_entry['id']} (pattern_id={pattern_id}); dropping log",
            )
            delete_logs([log_entry["id"]])
            return True

        if not _pattern_regex_matches_message(pattern, tokenized_message):
            log_info(
                logger,
                f"[INFO] No regex match for current log; requesting AI classification for pattern {pattern_id}",
            )

            ai_pattern = _classify_until_regex_matches(
                pattern_id,
                normalized_pattern,
                tokenized_message,
                host=log_entry.get("host"),
                program=log_entry.get("program"),
            )

            if ai_pattern:
                pattern = ai_pattern
                log_info(
                    logger,
                    f"[INFO] AI classification accepted for pattern {pattern_id}: '{pattern.get('classification')}' title='{pattern.get('title', '')}'",
                )
            else:
                # After max retries, drop this log and continue with remaining logs.
                log_error(
                    logger,
                    f"[ERROR] AI could not produce a matching regex for pattern {pattern_id}; dropping log {log_entry['id']} after max retries",
                )
                delete_logs([log_entry["id"]])
                return True

    # Record hourly stats for this pattern
    increment_pattern_stat(pattern_id, log_entry["received_at"])

    # Check effective classification — silently drop noise logs
    effective = get_effective_classification(pattern)

    if effective == "noise":
        increment_noise_stat(log_entry["received_at"])
        delete_logs([log_entry["id"]])
        return True

    # Persist inbound non-noise syslogs when explicitly enabled.
    write_syslog_daily_log(logger, log_entry.get("raw_message") or message)

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

        increment_host_alert_count(log_entry.get("host"), log_entry.get("source_ip"))

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
                log_error(
                    logger,
                    "[ERROR] Stopping log processing — AI unavailable. Will retry next cycle.",
                )
                return
        except Exception as e:
            log_error(
                logger,
                f"[ERROR] Error processing log {log_entry.get('id')}: {type(e).__name__}: {e}",
            )
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
        log_error(
            logger,
            "[FATAL] Processor cannot start without a working AI connection. Fix AI_API_BASE_URL, AI_API_KEY, and AI_MODEL then restart.",
        )
        sys.exit(1)
    log_info(logger, "[INFO] AI API connection successful")

    while True:
        try:
            process_logs()
        except Exception as e:
            log_error(logger, f"[ERROR] Processor error: {type(e).__name__}: {e}")

        time.sleep(PROCESS_INTERVAL)
