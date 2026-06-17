import logging
import socket
import threading
import time

from src.core.config import MITE_SYSLOG_TCP_HOST, MITE_SYSLOG_TCP_PORT
from src.core.db import (
    connect_to_db,
    disconnect_from_db,
    get_setting,
    insert_logs_batch,
    upsert_hosts_batch,
)
from src.core.syslog_parser import parse_syslog_message
from src.utils.locallogging import log_error, log_info

BUFFER_SIZE = 65535
TCP_BATCH_SIZE_DEFAULT = 500
TCP_BATCH_FLUSH_INTERVAL_DEFAULT = 1.0
BATCH_SIZE = TCP_BATCH_SIZE_DEFAULT
BATCH_FLUSH_INTERVAL = TCP_BATCH_FLUSH_INTERVAL_DEFAULT


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


def _flush_batch(logger, log_batch, host_batch, conn):
    try:
        insert_logs_batch(log_batch, conn=conn)
        upsert_hosts_batch(host_batch, conn=conn)
    except Exception as e:
        log_error(logger, f"[ERROR] TCP batch flush error: {e}")


def handle_tcp_client(conn_sock, addr):
    logger = logging.getLogger(__name__)
    source_ip = addr[0]
    buffer = ""
    log_batch = []
    host_batch = []
    last_flush = time.monotonic()

    db_conn = connect_to_db()

    try:
        conn_sock.settimeout(BATCH_FLUSH_INTERVAL)
        while True:
            try:
                data = conn_sock.recv(BUFFER_SIZE)
            except socket.timeout:
                if log_batch:
                    _flush_batch(logger, log_batch, host_batch, db_conn)
                    log_batch = []
                    host_batch = []
                    last_flush = time.monotonic()
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
                host_batch.append((parsed["host"], source_ip, parsed["received_at"]))

                if (
                    len(log_batch) >= BATCH_SIZE
                    or (time.monotonic() - last_flush) >= BATCH_FLUSH_INTERVAL
                ):
                    _flush_batch(logger, log_batch, host_batch, db_conn)
                    log_batch = []
                    host_batch = []
                    last_flush = time.monotonic()

    except Exception as e:
        log_error(logger, f"[ERROR] TCP client handler error ({source_ip}): {e}")
    finally:
        # Flush remaining
        if log_batch:
            _flush_batch(logger, log_batch, host_batch, db_conn)
        conn_sock.close()
        disconnect_from_db(db_conn)


def run_tcp_listener():
    global BATCH_SIZE, BATCH_FLUSH_INTERVAL
    logger = logging.getLogger(__name__)
    BATCH_SIZE = _get_int_setting("tcp_batch_size", TCP_BATCH_SIZE_DEFAULT)
    BATCH_FLUSH_INTERVAL = _get_float_setting(
        "tcp_batch_flush_interval_seconds", TCP_BATCH_FLUSH_INTERVAL_DEFAULT
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MITE_SYSLOG_TCP_HOST, MITE_SYSLOG_TCP_PORT))
    sock.listen(50)

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
