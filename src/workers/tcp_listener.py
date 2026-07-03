import logging
import socket
import threading
import time

from src.core.config import MITE_SYSLOG_TCP_HOST, MITE_SYSLOG_TCP_PORT
from src.core.constants import (
    DEFAULT_TCP_BATCH_FLUSH_INTERVAL_SECONDS,
    DEFAULT_TCP_BATCH_SIZE,
    SYSLOG_BUFFER_SIZE,
    SYSLOG_TCP_LISTEN_BACKLOG,
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
TCP_BATCH_SIZE_DEFAULT = DEFAULT_TCP_BATCH_SIZE
TCP_BATCH_FLUSH_INTERVAL_DEFAULT = DEFAULT_TCP_BATCH_FLUSH_INTERVAL_SECONDS


def _flush_batch(logger, log_batch, conn):
    try:
        insert_logs_batch(log_batch, conn=conn)
    except Exception as e:
        log_error(logger, f"[ERROR] TCP batch flush error: {e}")


def handle_tcp_client(conn_sock, addr, batch_size, batch_flush_interval):
    logger = logging.getLogger(__name__)
    source_ip = addr[0]
    buffer = ""
    log_batch = []
    last_flush = time.monotonic()
    last_filter_refresh = time.monotonic()

    # In-memory counter for logs silently dropped at the listener. Flushed to
    # the DB on the same cadence as the log batch, then reset to 0.
    dropped_count = 0
    dropped_ts = None

    # In-memory counter for logs dropped for being too small / low-signal.
    too_small_count = 0
    too_small_ts = None

    db_conn = connect_to_db(get_db_for_table("logs"))

    # Load filter patterns
    refresh_filter_cache()

    try:
        conn_sock.settimeout(batch_flush_interval)
        while True:
            try:
                data = conn_sock.recv(BUFFER_SIZE)
            except socket.timeout:
                if log_batch:
                    _flush_batch(logger, log_batch, db_conn)
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

            if not data:
                break

            buffer += data.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                parsed = parse_syslog_message(line, source_ip=source_ip)

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
                if (
                    len(log_batch) >= batch_size
                    or (time.monotonic() - last_flush) >= batch_flush_interval
                ):
                    _flush_batch(logger, log_batch, db_conn)
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
        log_error(logger, f"[ERROR] TCP client handler error ({source_ip}): {e}")
    finally:
        # Flush remaining
        if log_batch:
            _flush_batch(logger, log_batch, db_conn)
        if dropped_count:
            record_silently_dropped(dropped_count, dropped_ts)
        if too_small_count:
            record_discarded_too_small(too_small_count, too_small_ts)
        conn_sock.close()
        disconnect_from_db(db_conn)


def run_tcp_listener():
    logger = logging.getLogger(__name__)
    batch_size = get_int_setting("tcp_batch_size", TCP_BATCH_SIZE_DEFAULT)
    batch_flush_interval = get_float_setting(
        "tcp_batch_flush_interval_seconds", TCP_BATCH_FLUSH_INTERVAL_DEFAULT
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MITE_SYSLOG_TCP_HOST, MITE_SYSLOG_TCP_PORT))
    sock.listen(SYSLOG_TCP_LISTEN_BACKLOG)

    log_info(
        logger,
        f"[INFO] TCP syslog listener started on {MITE_SYSLOG_TCP_HOST}:{MITE_SYSLOG_TCP_PORT}",
    )

    while True:
        try:
            conn, addr = sock.accept()
            thread = threading.Thread(
                target=handle_tcp_client,
                args=(conn, addr, batch_size, batch_flush_interval),
                daemon=True,
            )
            thread.start()
        except Exception as e:
            log_error(logger, f"[ERROR] TCP listener error: {e}")


if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    log_info(logger, "[INFO] TCP listener process starting, waiting 5 seconds...")
    time.sleep(5)

    from src.core.db import init_database

    init_database()

    run_tcp_listener()
