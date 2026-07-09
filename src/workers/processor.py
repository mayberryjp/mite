import logging
import re
import sys
import time

from src.core.ai_discovery import (
    classify_single_pattern,
    is_ai_rate_limited,
    preprocess_sample_for_ai,
    test_ai_connection,
)
from src.core.constants import (
    DEFAULT_PROCESSOR_FETCH_LIMIT,
    DEFAULT_PROCESSOR_INTERVAL_SECONDS,
)
from src.core.constants import MAX_AI_REGEX_ATTEMPTS as MAX_AI_REGEX_ATTEMPTS_CONST
from src.core.db import (
    create_action,
    delete_logs,
    get_pattern_by_hash,
    get_pattern_by_id,
    get_patterns_with_regex,
    get_setting,
    get_unprocessed_logs,
    increment_noise_stat,
    increment_pattern_hit,
    increment_pattern_stat,
    insert_alert,
    insert_pattern,
    mark_logs_processed,
    update_alert_discord_sent,
)
from src.core.discord import send_alert_discord, send_discord_message
from src.core.pattern_extractor import extract_pattern, hash_pattern
from src.core.settings_loader import get_int_setting
from src.core.syslog_forwarder import CLASSIFICATION_LEVELS, forward_log_to_syslog
from src.utils.locallogging import log_error, log_info, log_warn, write_syslog_daily_log

logger = logging.getLogger(__name__)

# Import constants and set up initial module variables
PROCESS_INTERVAL_DEFAULT = DEFAULT_PROCESSOR_INTERVAL_SECONDS
PROCESS_INTERVAL = PROCESS_INTERVAL_DEFAULT
PROCESS_FETCH_LIMIT_DEFAULT = DEFAULT_PROCESSOR_FETCH_LIMIT
PROCESS_FETCH_LIMIT = PROCESS_FETCH_LIMIT_DEFAULT
REGEX_CACHE_TTL_DEFAULT = 60
MAX_AI_REGEX_ATTEMPTS = MAX_AI_REGEX_ATTEMPTS_CONST
SYSLOG_FORWARD_ENABLED = False
SYSLOG_FORWARD_DESTINATION = ""
SYSLOG_FORWARD_MIN_CLASSIFICATION = "low"
WRITE_SYSLOG_MIN_CLASSIFICATION = "low"
DB_STORE_MIN_CLASSIFICATION = "low"


def _load_syslog_forwarding_settings():
    """Load syslog forwarding settings from the database."""
    global SYSLOG_FORWARD_ENABLED, SYSLOG_FORWARD_DESTINATION, SYSLOG_FORWARD_MIN_CLASSIFICATION, WRITE_SYSLOG_MIN_CLASSIFICATION, DB_STORE_MIN_CLASSIFICATION

    # Load enabled flag
    enabled_str = get_setting("syslog_forward_enabled", "false")
    SYSLOG_FORWARD_ENABLED = str(enabled_str).strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )

    # Load destination
    SYSLOG_FORWARD_DESTINATION = (
        get_setting("syslog_forward_destination", "") or ""
    ).strip()

    # Load minimum classification level
    min_class = get_setting("syslog_forward_min_classification", "low") or "low"
    SYSLOG_FORWARD_MIN_CLASSIFICATION = str(min_class).strip().lower()

    write_min = get_setting("write_syslog_min_classification", "low") or "low"
    WRITE_SYSLOG_MIN_CLASSIFICATION = str(write_min).strip().lower()

    store_min = get_setting("db_store_min_classification", "low") or "low"
    DB_STORE_MIN_CLASSIFICATION = str(store_min).strip().lower()


# Cache of compiled regexes, refreshed periodically
_regex_cache = []
_regex_cache_time = 0
REGEX_CACHE_TTL = REGEX_CACHE_TTL_DEFAULT


def _meets_min_classification(classification, min_classification):
    """Return True if classification is at or above min_classification level."""
    try:
        level = CLASSIFICATION_LEVELS.index((classification or "").lower())
        min_level = CLASSIFICATION_LEVELS.index((min_classification or "").lower())
        return level >= min_level
    except ValueError:
        return True


def _load_runtime_settings():
    """Load processor runtime tuning settings from DB with safe fallbacks."""
    global PROCESS_INTERVAL, PROCESS_FETCH_LIMIT, REGEX_CACHE_TTL

    PROCESS_INTERVAL = get_int_setting(
        "processor_interval_seconds", PROCESS_INTERVAL_DEFAULT
    )
    PROCESS_FETCH_LIMIT = get_int_setting(
        "processor_fetch_limit", PROCESS_FETCH_LIMIT_DEFAULT
    )
    REGEX_CACHE_TTL = get_int_setting(
        "regex_cache_ttl_seconds", REGEX_CACHE_TTL_DEFAULT
    )
    _load_syslog_forwarding_settings()


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
        except re.error:
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


def _is_setting_enabled(key, default="false", legacy_key=None):
    value = get_setting(key)
    if value is None and legacy_key:
        value = get_setting(legacy_key)
    if value is None:
        value = default
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _handle_new_pattern_side_effects(pattern_id, pattern, tokenized_message):
    action_enabled = _is_setting_enabled(
        "action_on_new_patterns", default="true", legacy_key="action_on_new_pattern"
    )
    notify_enabled = _is_setting_enabled(
        "notify_on_new_patterns", legacy_key="notify_on_new_pattern"
    )

    if action_enabled:
        action_name = f"pattern_{pattern_id}"
        action_text = f"New pattern created: id={pattern_id}, name={action_name}"
        create_action(action_text, acknowledged=False)

    if notify_enabled:
        title = (pattern or {}).get("title") or f"pattern_{pattern_id}"
        host = (pattern or {}).get("host") or "unknown"
        program = (pattern or {}).get("program") or "unknown"
        classification = (pattern or {}).get("classification") or "pending"
        content = (
            "Mite New Pattern\n\n"
            f"Pattern ID: {pattern_id}\n"
            f"Title: {title}\n"
            f"Classification: {classification}\n"
            f"Host: {host}\n"
            f"Program: {program}\n\n"
            "Sample:\n"
            f"{_truncate_for_log(tokenized_message, 800)}"
        )
        send_discord_message(content)


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
    log_info(
        logger,
        f"[INFO] Pattern {pattern_id} tokenized message sent to AI: {debug_preprocessed_message!r}",
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
            log_info(
                logger,
                f"[INFO] Pattern {pattern_id} retry feedback sent to AI (attempt {attempt}/{MAX_AI_REGEX_ATTEMPTS})",
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
            log_warn(
                logger,
                f"[WARN] AI classification returned no result for pattern {pattern_id} (attempt {attempt}/{MAX_AI_REGEX_ATTEMPTS})",
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
        log_warn(
            logger,
            f"[WARN] AI regex did not match source log for pattern {pattern_id} (attempt {attempt}/{MAX_AI_REGEX_ATTEMPTS}); retrying",
        )
        log_info(logger, f"[INFO] Pattern {pattern_id} regex: {debug_regex!r}")
        log_info(logger, f"[INFO] Pattern {pattern_id} message: {debug_message!r}")

    return None


def process_log(log_entry):
    """Process a single log entry.

    Logs matching an existing pattern are always processed. An unclassified log
    that needs AI is skipped (left unprocessed for retry) when the AI budget is
    exhausted, so a rate limit never blocks logs that match existing patterns.
    """

    message = log_entry.get("message", "")
    tokenized_message = preprocess_sample_for_ai(message)
    normalized_pattern = extract_pattern(tokenized_message)

    # Step 1: Try matching against AI-provided regexes from known patterns
    regex_match_id, regex_classification = match_by_regex(tokenized_message)
    pattern = None
    pattern_id = None
    new_pattern_created = False

    if regex_match_id:
        # Matched an existing pattern via regex
        pattern_id = regex_match_id
        increment_pattern_hit(pattern_id, log_entry["received_at"])
        if regex_classification == "noise":
            increment_pattern_stat(pattern_id, log_entry["received_at"])
            increment_noise_stat(log_entry["received_at"])
            if _meets_min_classification("noise", DB_STORE_MIN_CLASSIFICATION):
                mark_logs_processed([log_entry["id"]], pattern_id=pattern_id)
            else:
                delete_logs([log_entry["id"]])
            return True
        pattern = get_pattern_by_id(pattern_id)
        if not pattern:
            log_warn(
                logger,
                f"[WARN] Regex matched missing pattern id={pattern_id}; invalidating cache and leaving log {log_entry['id']} for retry",
            )
            _invalidate_regex_cache()
            return True
    else:
        # Deterministic pattern identity prevents duplicate patterns for equivalent logs.
        pattern_hash = hash_pattern(normalized_pattern)
        pattern = get_pattern_by_hash(pattern_hash)

        # If AI classification will be required (a brand-new pattern, or an
        # existing one whose regex does not match) but the AI budget is
        # exhausted, skip just this log: create nothing, drop nothing, and leave
        # it unprocessed so it is retried once AI calls are available again.
        # Logs that match an existing pattern keep flowing in the same cycle.
        needs_ai = pattern is None or not _pattern_regex_matches_message(
            pattern, tokenized_message
        )
        if needs_ai and is_ai_rate_limited():
            log_warn(
                logger,
                f"[WARN] AI rate limit reached; skipping unclassified log "
                f"{log_entry['id']} (left unprocessed for retry). Logs matching "
                "existing patterns continue to be processed.",
            )
            return True

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
            new_pattern_created = True

        if not pattern:
            log_warn(
                logger,
                f"[WARN] Pattern lookup failed for log {log_entry['id']} (pattern_id={pattern_id}); leaving it unprocessed for retry",
            )
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
                # AI is reachable but could not produce a regex matching this log
                # after retries. Keep the log linked to its pattern (the AI worker
                # will finish classifying it if still pending) rather than dropping it.
                log_warn(
                    logger,
                    f"[WARN] AI could not produce a matching regex for pattern {pattern_id}; keeping log {log_entry['id']} linked to the pattern",
                )
                refreshed = get_pattern_by_id(pattern_id)
                if refreshed:
                    pattern = refreshed

    if new_pattern_created:
        _handle_new_pattern_side_effects(pattern_id, pattern, tokenized_message)

    # Record hourly stats for this pattern
    increment_pattern_stat(pattern_id, log_entry["received_at"])

    # Check effective classification
    effective = get_effective_classification(pattern)

    if effective == "noise":
        increment_noise_stat(log_entry["received_at"])

    # Drop logs below the minimum classification level for DB storage.
    if not _meets_min_classification(effective, DB_STORE_MIN_CLASSIFICATION):
        delete_logs([log_entry["id"]])
        return True

    # Persist inbound syslogs to disk only at or above the configured level.
    if _meets_min_classification(effective, WRITE_SYSLOG_MIN_CLASSIFICATION):
        write_syslog_daily_log(logger, log_entry.get("raw_message") or message)

    if effective == "critical":
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

        if alert_id:
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

    # Forward log if syslog forwarding is enabled
    if SYSLOG_FORWARD_ENABLED and SYSLOG_FORWARD_DESTINATION:
        forward_log_to_syslog(
            message=log_entry.get("raw_message") or message,
            destination_str=SYSLOG_FORWARD_DESTINATION,
            log_classification=effective,
            min_classification=SYSLOG_FORWARD_MIN_CLASSIFICATION,
        )

    # Mark log as processed with pattern link
    mark_logs_processed([log_entry["id"]], pattern_id=pattern_id)
    return True


def process_logs():
    _load_runtime_settings()
    logs = get_unprocessed_logs(limit=PROCESS_FETCH_LIMIT)
    if not logs:
        log_info(logger, "[INFO] No logs to process")
        return

    log_info(logger, f"[INFO] Processing {len(logs)} unprocessed logs")

    start_time = time.monotonic()

    for log_entry in logs:
        try:
            process_log(log_entry)
        except Exception as e:
            log_error(
                logger,
                f"[ERROR] Error processing log {log_entry.get('id')}: {type(e).__name__}: {e}",
            )
            # Don't mark as processed — retry next cycle
    elapsed = time.monotonic() - start_time
    log_info(logger, f"[INFO] Processed {len(logs)} logs in {elapsed:.2f} seconds")


if __name__ == "__main__":
    log_info(logger, "[INFO] Log processor starting, waiting 10 seconds...")
    time.sleep(10)

    from src.core.db import init_database

    init_database()
    _load_runtime_settings()

    # Test AI connectivity at startup — fail hard if not configured
    log_info(logger, "[INFO] Testing AI API connectivity...")
    success, error = test_ai_connection()
    if not success:
        log_error(
            logger,
            f"[FATAL] AI API is not available: {error}. Processor cannot start "
            "without a working AI connection. Fix AI_API_BASE_URL, AI_API_KEY, "
            "and AI_MODEL then restart.",
        )
        sys.exit(1)
    log_info(logger, "[INFO] AI API connection successful")

    while True:
        try:
            process_logs()
        except Exception as e:
            log_error(logger, f"[ERROR] Processor error: {type(e).__name__}: {e}")

        time.sleep(PROCESS_INTERVAL)
