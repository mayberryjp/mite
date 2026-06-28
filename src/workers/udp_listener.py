import logging
import re
import socket
import time

from src.core.config import MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT
from src.core.db import (
    connect_to_db,
    disconnect_from_db,
    get_filter_patterns,
    get_setting,
    insert_logs_batch,
)
from src.core.syslog_parser import parse_syslog_message
from src.utils.locallogging import log_error, log_info

BUFFER_SIZE = 65535
UDP_BATCH_SIZE_DEFAULT = 500
UDP_BATCH_FLUSH_INTERVAL_DEFAULT = 1.0
UDP_RECV_BUFFER_DEFAULT = 4 * 1024 * 1024  # 4 MB


def _get_int_setting(key, default_value, min_value=1):
    raw_value = get_setting(key, str(default_value))
    try:
        parsed = int(raw_value)
        if parsed < min_value:
            raise ValueError(f"{key} must be >= {min_value}")
        return parsed
    except (TypeError, ValueError):
        log_error(
            logging.getLogger(__name__),
            f"[ERROR] Invalid setting '{key}' value '{raw_value}', using default {default_value}",
        )
        return default_value


def _get_float_setting(key, default_value, min_value=0.1):
    raw_value = get_setting(key, str(default_value))
    try:
        parsed = float(raw_value)
        if parsed < min_value:
            raise ValueError(f"{key} must be >= {min_value}")
        return parsed
    except (TypeError, ValueError):
        log_error(
            logging.getLogger(__name__),
            f"[ERROR] Invalid setting '{key}' value '{raw_value}', using default {default_value}",
        )
        return default_value


# Cache of filter patterns (patterns with filter_at_listener = 1)
_filter_cache = []
_filter_cache_ttl = 60  # seconds


def _refresh_filter_cache():
    """Load and compile patterns marked for filtering at listener."""
    global _filter_cache
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


def run_udp_listener():
    logger = logging.getLogger(__name__)
    batch_size = _get_int_setting("udp_batch_size", UDP_BATCH_SIZE_DEFAULT)
    batch_flush_interval = _get_float_setting(
        "udp_batch_flush_interval_seconds", UDP_BATCH_FLUSH_INTERVAL_DEFAULT
    )
    udp_recv_buffer = _get_int_setting(
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

    conn = connect_to_db()

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

        except Exception as e:
            log_error(logger, f"[ERROR] UDP listener error: {e}")
            # Reconnect on DB errors
            try:
                disconnect_from_db(conn)
            except Exception:
                pass
            conn = connect_to_db()


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
