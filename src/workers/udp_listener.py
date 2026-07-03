import logging
import socket
import time

from src.core.config import MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT
from src.core.constants import (
    DEFAULT_UDP_BATCH_FLUSH_INTERVAL_SECONDS,
    DEFAULT_UDP_BATCH_SIZE,
    SYSLOG_BUFFER_SIZE,
    SYSLOG_UDP_RECV_BUFFER_SIZE,
)
from src.core.db import (
    connect_to_db,
    disconnect_from_db,
    get_db_for_table,
    insert_logs_batch,
    record_discarded_too_small,
    record_silently_dropped,
)
from src.core.settings_loader import get_float_setting, get_int_setting
from src.core.syslog_parser import parse_syslog_message
from src.utils.locallogging import log_error, log_info
from src.workers.listener_common import (
    FILTER_CACHE_TTL,
    is_meaningful_message,
    refresh_filter_cache,
    should_filter_message,
)

# Use constants for default values; these can be overridden by database settings
BUFFER_SIZE = SYSLOG_BUFFER_SIZE
UDP_BATCH_SIZE_DEFAULT = DEFAULT_UDP_BATCH_SIZE
UDP_BATCH_FLUSH_INTERVAL_DEFAULT = DEFAULT_UDP_BATCH_FLUSH_INTERVAL_SECONDS
UDP_RECV_BUFFER_DEFAULT = SYSLOG_UDP_RECV_BUFFER_SIZE  # 4 MB


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
    refresh_filter_cache()
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
                if time.monotonic() - last_filter_refresh > FILTER_CACHE_TTL:
                    refresh_filter_cache()
                    last_filter_refresh = time.monotonic()
                continue

            source_ip = addr[0]
            raw_line = data.decode("utf-8", errors="replace").strip()

            if not raw_line:
                continue

            parsed = parse_syslog_message(raw_line, source_ip=source_ip)

            # Check if message matches any filter pattern
            if should_filter_message(parsed["message"]):
                dropped_count += 1
                dropped_ts = parsed["received_at"]
                continue

            # Drop messages that are too small / low-signal
            if not is_meaningful_message(parsed["message"]):
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
