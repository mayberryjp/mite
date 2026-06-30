import logging
import re
import socket
import time

from src.core.config import MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT
from src.core.constants import (
    DEFAULT_UDP_BATCH_FLUSH_INTERVAL_SECONDS,
    DEFAULT_UDP_BATCH_SIZE,
    FILTER_CACHE_TTL_SECONDS,
    MIN_MESSAGE_LENGTH,
    SYSLOG_BUFFER_SIZE,
    SYSLOG_UDP_RECV_BUFFER_SIZE,
)
from src.core.db import (
    connect_to_db,
    disconnect_from_db,
    get_db_for_table,
    get_filter_patterns,
    insert_logs_batch,
    record_discarded_too_small,
    record_silently_dropped,
)
from src.core.settings_loader import get_float_setting, get_int_setting
from src.core.syslog_parser import parse_syslog_message
from src.utils.locallogging import log_error, log_info

# Use constants for default values; these can be overridden by database settings
BUFFER_SIZE = SYSLOG_BUFFER_SIZE
UDP_BATCH_SIZE_DEFAULT = DEFAULT_UDP_BATCH_SIZE
UDP_BATCH_FLUSH_INTERVAL_DEFAULT = DEFAULT_UDP_BATCH_FLUSH_INTERVAL_SECONDS
UDP_RECV_BUFFER_DEFAULT = SYSLOG_UDP_RECV_BUFFER_SIZE  # 4 MB


# Cache of filter patterns (patterns with filter_at_listener = 1)
_filter_cache = []
_filter_cache_ttl = FILTER_CACHE_TTL_SECONDS

# Minimum meaningful message length, refreshed alongside the filter cache.
_min_message_length = MIN_MESSAGE_LENGTH


def _refresh_filter_cache():
    """Load and compile patterns marked for filtering at listener."""
    global _filter_cache, _min_message_length
    try:
        _min_message_length = get_int_setting(
            "min_message_length", MIN_MESSAGE_LENGTH, min_value=0
        )
    except Exception:
        _min_message_length = MIN_MESSAGE_LENGTH
    try:
        patterns = get_filter_patterns()
        compiled = []
        for p in patterns:
            try:
                compiled.append(
                    {
                        "id": p["id"],
                        "regex": re.compile(p["match_regex"]),
                    }
                )
            except re.error as e:
                logging.getLogger(__name__).warning(
                    f"[WARN] Invalid regex for filter pattern {p['id']}: {e}"
                )
        _filter_cache = compiled
    except Exception as e:
        logging.getLogger(__name__).error(
            f"[ERROR] Failed to load filter patterns: {e}"
        )


def _should_filter_message(message):
    """Check if message matches any filter pattern. Returns True if should be filtered."""
    if not _filter_cache:
        return False
    for entry in _filter_cache:
        try:
            if entry["regex"].search(message):
                return True
        except Exception:
            pass
    return False


def _is_meaningful_message(message):
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


def run_udp_listener():
    logger = logging.getLogger(__name__)
    batch_size = get_int_setting("udp_batch_size", UDP_BATCH_SIZE_DEFAULT)
    batch_flush_interval = get_float_setting(
        "udp_batch_flush_interval_seconds", UDP_BATCH_FLUSH_INTERVAL_DEFAULT
    )
    udp_recv_buffer = get_int_setting(
        "udp_recv_buffer_bytes", UDP_RECV_BUFFER_DEFAULT, min_value=65536
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Increase OS receive buffer to reduce packet drops under load
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, udp_recv_buffer)
        actual = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        log_info(logger, f"[INFO] UDP receive buffer set to {actual} bytes")
    except OSError as e:
        log_error(logger, f"[ERROR] Could not set UDP receive buffer: {e}")

    sock.settimeout(batch_flush_interval)
    sock.bind((MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT))

    log_info(
        logger,
        f"[INFO] UDP syslog listener started on {MITE_SYSLOG_UDP_HOST}:{MITE_SYSLOG_UDP_PORT}",
    )

    log_batch = []
    last_flush = time.monotonic()

    # In-memory counter for logs silently dropped at the listener. Flushed to
    # the DB on the same cadence as the log batch, then reset to 0.
    dropped_count = 0
    dropped_ts = None

    # In-memory counter for logs dropped for being too small / low-signal.
    too_small_count = 0
    too_small_ts = None

    conn = connect_to_db(get_db_for_table("logs"))

    # Load filter patterns
    _refresh_filter_cache()
    last_filter_refresh = time.monotonic()

    while True:
        try:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                # Flush whatever we have on timeout
                if log_batch:
                    _flush_batch(logger, log_batch, conn)
                    log_batch = []
                    last_flush = time.monotonic()
                if dropped_count:
                    record_silently_dropped(dropped_count, dropped_ts)
                    dropped_count = 0
                    dropped_ts = None
                if too_small_count:
                    record_discarded_too_small(too_small_count, too_small_ts)
                    too_small_count = 0
                    too_small_ts = None
                # Refresh filter cache periodically
                if time.monotonic() - last_filter_refresh > _filter_cache_ttl:
                    _refresh_filter_cache()
                    last_filter_refresh = time.monotonic()
                continue

            source_ip = addr[0]
            raw_line = data.decode("utf-8", errors="replace").strip()

            if not raw_line:
                continue

            parsed = parse_syslog_message(raw_line, source_ip=source_ip)

            # Check if message matches any filter pattern
            if _should_filter_message(parsed["message"]):
                dropped_count += 1
                dropped_ts = parsed["received_at"]
                continue

            # Drop messages that are too small / low-signal
            if not _is_meaningful_message(parsed["message"]):
                too_small_count += 1
                too_small_ts = parsed["received_at"]
                continue

            log_batch.append(
                (
                    parsed["received_at"],
                    parsed["source_ip"],
                    parsed["host"],
                    parsed["facility"],
                    parsed["severity"],
                    parsed["program"],
                    parsed["pid"],
                    parsed["message"],
                    parsed["raw_message"],
                )
            )
            # Flush when batch is full or interval elapsed
            if (
                len(log_batch) >= batch_size
                or (time.monotonic() - last_flush) >= batch_flush_interval
            ):
                _flush_batch(logger, log_batch, conn)
                log_batch = []
                last_flush = time.monotonic()
                if dropped_count:
                    record_silently_dropped(dropped_count, dropped_ts)
                    dropped_count = 0
                    dropped_ts = None
                if too_small_count:
                    record_discarded_too_small(too_small_count, too_small_ts)
                    too_small_count = 0
                    too_small_ts = None

        except Exception as e:
            log_error(logger, f"[ERROR] UDP listener error: {e}")
            # Reconnect on DB errors
            try:
                disconnect_from_db(conn)
            except Exception:
                pass
            conn = connect_to_db(get_db_for_table("logs"))


def _flush_batch(logger, log_batch, conn):
    try:
        insert_logs_batch(log_batch, conn=conn)
    except Exception as e:
        log_error(logger, f"[ERROR] UDP batch flush error: {e}")


if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    log_info(logger, "[INFO] UDP listener process starting, waiting 5 seconds...")
    time.sleep(5)

    from src.core.db import init_database

    init_database()

    run_udp_listener()
