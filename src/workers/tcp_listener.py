import logging
import re
import socket
import threading
import time

from src.core.config import MITE_SYSLOG_TCP_HOST, MITE_SYSLOG_TCP_PORT
from src.core.constants import (
    DEFAULT_TCP_BATCH_FLUSH_INTERVAL_SECONDS,
    DEFAULT_TCP_BATCH_SIZE,
    FILTER_CACHE_TTL_SECONDS,
    SYSLOG_BUFFER_SIZE,
    SYSLOG_TCP_LISTEN_BACKLOG,
)
from src.core.db import (
    connect_to_db,
    disconnect_from_db,
    get_filter_patterns,
    insert_logs_batch,
)
from src.core.settings_loader import get_float_setting, get_int_setting
from src.core.syslog_parser import parse_syslog_message
from src.utils.locallogging import log_error, log_info

# Use constants for default values; these can be overridden by database settings
BUFFER_SIZE = SYSLOG_BUFFER_SIZE
TCP_BATCH_SIZE_DEFAULT = DEFAULT_TCP_BATCH_SIZE
TCP_BATCH_FLUSH_INTERVAL_DEFAULT = DEFAULT_TCP_BATCH_FLUSH_INTERVAL_SECONDS


# Cache of filter patterns (patterns with filter_at_listener = 1)
_filter_cache = []
_filter_cache_ttl = FILTER_CACHE_TTL_SECONDS


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


def _flush_batch(logger, log_batch, conn):
    try:
        insert_logs_batch(log_batch, conn=conn)
    except Exception as e:
        log_error(logger, f"[ERROR] TCP batch flush error: {e}")


def handle_tcp_client(conn_sock, addr):
    logger = logging.getLogger(__name__)
    source_ip = addr[0]
    buffer = ""
    log_batch = []
    last_flush = time.monotonic()
    last_filter_refresh = time.monotonic()

    db_conn = connect_to_db()

    # Load filter patterns
    _refresh_filter_cache()

    try:
        conn_sock.settimeout(BATCH_FLUSH_INTERVAL)
        while True:
            try:
                data = conn_sock.recv(BUFFER_SIZE)
            except socket.timeout:
                if log_batch:
                    _flush_batch(logger, log_batch, db_conn)
                    log_batch = []
                    last_flush = time.monotonic()
                # Refresh filter cache periodically
                if time.monotonic() - last_filter_refresh > _filter_cache_ttl:
                    _refresh_filter_cache()
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
                if (
                    len(log_batch) >= BATCH_SIZE
                    or (time.monotonic() - last_flush) >= BATCH_FLUSH_INTERVAL
                ):
                    _flush_batch(logger, log_batch, db_conn)
                    log_batch = []
                    last_flush = time.monotonic()

    except Exception as e:
        log_error(logger, f"[ERROR] TCP client handler error ({source_ip}): {e}")
    finally:
        # Flush remaining
        if log_batch:
            _flush_batch(logger, log_batch, db_conn)
        conn_sock.close()
        disconnect_from_db(db_conn)


def run_tcp_listener():
    global BATCH_SIZE, BATCH_FLUSH_INTERVAL
    logger = logging.getLogger(__name__)
    BATCH_SIZE = get_int_setting("tcp_batch_size", TCP_BATCH_SIZE_DEFAULT)
    BATCH_FLUSH_INTERVAL = get_float_setting(
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
                target=handle_tcp_client, args=(conn, addr), daemon=True
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
