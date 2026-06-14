import logging
import socket
import time

from src.core.config import MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT
from src.core.syslog_parser import parse_syslog_message
from src.core.db import insert_logs_batch, upsert_hosts_batch, connect_to_db, disconnect_from_db
from src.utils.locallogging import log_error, log_info

BUFFER_SIZE = 65535
BATCH_SIZE = 500
BATCH_FLUSH_INTERVAL = 1.0  # seconds
UDP_RECV_BUFFER = 4 * 1024 * 1024  # 4 MB


def run_udp_listener():
    logger = logging.getLogger(__name__)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Increase OS receive buffer to reduce packet drops under load
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECV_BUFFER)
        actual = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        log_info(logger, f"[INFO] UDP receive buffer set to {actual} bytes")
    except OSError as e:
        log_error(logger, f"[ERROR] Could not set UDP receive buffer: {e}")

    sock.settimeout(BATCH_FLUSH_INTERVAL)
    sock.bind((MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT))

    log_info(logger, f"[INFO] UDP syslog listener started on {MITE_SYSLOG_UDP_HOST}:{MITE_SYSLOG_UDP_PORT}")

    log_batch = []
    host_batch = []
    last_flush = time.monotonic()

    conn = connect_to_db()

    while True:
        try:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                # Flush whatever we have on timeout
                if log_batch:
                    _flush_batch(logger, log_batch, host_batch, conn)
                    log_batch = []
                    host_batch = []
                    last_flush = time.monotonic()
                continue

            source_ip = addr[0]
            raw_line = data.decode("utf-8", errors="replace").strip()

            if not raw_line:
                continue

            parsed = parse_syslog_message(raw_line, source_ip=source_ip)
            log_batch.append((
                parsed["received_at"],
                parsed["source_ip"],
                parsed["host"],
                parsed["facility"],
                parsed["severity"],
                parsed["program"],
                parsed["pid"],
                parsed["message"],
                parsed["raw_message"],
            ))
            host_batch.append((parsed["host"], source_ip, parsed["received_at"]))

            # Flush when batch is full or interval elapsed
            if len(log_batch) >= BATCH_SIZE or (time.monotonic() - last_flush) >= BATCH_FLUSH_INTERVAL:
                _flush_batch(logger, log_batch, host_batch, conn)
                log_batch = []
                host_batch = []
                last_flush = time.monotonic()

        except Exception as e:
            log_error(logger, f"[ERROR] UDP listener error: {e}")
            # Reconnect on DB errors
            try:
                disconnect_from_db(conn)
            except Exception:
                pass
            conn = connect_to_db()


def _flush_batch(logger, log_batch, host_batch, conn):
    try:
        insert_logs_batch(log_batch, conn=conn)
        upsert_hosts_batch(host_batch, conn=conn)
    except Exception as e:
        log_error(logger, f"[ERROR] UDP batch flush error: {e}")


if __name__ == "__main__":
    import time

    logger = logging.getLogger(__name__)
    log_info(logger, "[INFO] UDP listener process starting, waiting 5 seconds...")
    time.sleep(5)

    from src.core.db import init_database
    init_database()

    run_udp_listener()
