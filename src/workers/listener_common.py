"""Shared helpers for the UDP and TCP syslog listeners.

Both listeners perform identical listener-side filtering: compile the
filter-at-listener pattern regexes, drop messages that match them, and drop
messages that are too small / low-signal. This module holds that logic once so
the two listener processes stay in sync. Each listener runs as its own process,
so the module-level cache below is per-process (and shared across the TCP
listener's per-connection threads, which is the intended behavior).
"""

import logging
import re

from src.core.constants import FILTER_CACHE_TTL_SECONDS, MIN_MESSAGE_LENGTH
from src.core.db import get_filter_patterns
from src.core.settings_loader import get_int_setting

logger = logging.getLogger(__name__)

# How long a compiled filter cache is kept before a refresh is due.
FILTER_CACHE_TTL = FILTER_CACHE_TTL_SECONDS

# Cache of compiled filter patterns (patterns with filter_at_listener = 1).
_filter_cache = []

# Minimum meaningful message length, refreshed alongside the filter cache.
_min_message_length = MIN_MESSAGE_LENGTH


def refresh_filter_cache():
    """Reload compiled filter patterns and the minimum meaningful message length.

    Best-effort: on a load failure the previous cache is kept and the error is
    logged rather than propagated, so a transient DB issue never kills a listener.
    """
    global _filter_cache, _min_message_length
    try:
        _min_message_length = get_int_setting(
            "min_message_length", MIN_MESSAGE_LENGTH, min_value=0
        )
        patterns = get_filter_patterns()
    except Exception as e:
        logger.error(f"[ERROR] Failed to load filter settings/patterns: {e}")
        return

    compiled = []
    for p in patterns:
        try:
            compiled.append({"id": p["id"], "regex": re.compile(p["match_regex"])})
        except re.error as e:
            logger.warning(f"[WARN] Invalid regex for filter pattern {p['id']}: {e}")
    _filter_cache = compiled


def should_filter_message(message):
    """Return True if the message matches any filter-at-listener pattern."""
    if not _filter_cache:
        return False
    for entry in _filter_cache:
        try:
            if entry["regex"].search(message):
                return True
        except re.error:
            continue
    return False


def is_meaningful_message(message):
    """Return False for low-signal messages that are too small to be worth keeping.

    Drops messages shorter than the configured minimum length or with fewer than
    three real alphabetic words.
    """
    text = (message or "").strip()
    if len(text) < _min_message_length:
        return False
    stripped = re.sub(r"[^A-Za-z\s]", " ", text).strip()
    words = [w for w in stripped.split() if len(w) > 1 and any(c.isalpha() for c in w)]
    return len(words) >= 3
