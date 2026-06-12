import logging
import socket
import threading

from src.core.config import MITE_SYSLOG_TCP_HOST, MITE_SYSLOG_TCP_PORT
from src.core.syslog_parser import parse_syslog_message
from src.core.db import insert_log, upsert_host
from src.utils.locallogging import log_error, log_info

BUFFER_SIZE = 65535


def handle_tcp_client(conn, addr):
    logger = logging.getLogger(__name__)
    source_ip = addr[0]
    buffer = ""

    try:
        while True:
            data = conn.recv(BUFFER_SIZE)
            if not data:
                break

            buffer += data.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                parsed = parse_syslog_message(line, source_ip=source_ip)
                insert_log(
                    received_at=parsed["received_at"],
                    source_ip=parsed["source_ip"],
                    host=parsed["host"],
                    facility=parsed["facility"],
                    severity=parsed["severity"],
                    program=parsed["program"],
                    pid=parsed["pid"],
                    message=parsed["message"],
                    raw_message=parsed["raw_message"],
                )
                upsert_host(parsed["host"], source_ip, parsed["received_at"])

    except Exception as e:
        log_error(logger, f"[ERROR] TCP client handler error ({source_ip}): {e}")
    finally:
        conn.close()


def run_tcp_listener():
    logger = logging.getLogger(__name__)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MITE_SYSLOG_TCP_HOST, MITE_SYSLOG_TCP_PORT))
    sock.listen(50)

    log_info(logger, f"[INFO] TCP syslog listener started on {MITE_SYSLOG_TCP_HOST}:{MITE_SYSLOG_TCP_PORT}")

    while True:
        try:
            conn, addr = sock.accept()
            thread = threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True)
            thread.start()
        except Exception as e:
            log_error(logger, f"[ERROR] TCP listener error: {e}")


if __name__ == "__main__":
    import time

    logger = logging.getLogger(__name__)
    log_info(logger, "[INFO] TCP listener process starting, waiting 5 seconds...")
    time.sleep(5)

    from src.core.db import init_database
    init_database()

    run_tcp_listener()
