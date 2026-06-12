import logging
import socket

from src.core.config import MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT
from src.core.syslog_parser import parse_syslog_message
from src.core.db import insert_log, upsert_host
from src.utils.locallogging import log_error, log_info

BUFFER_SIZE = 65535


def run_udp_listener():
    logger = logging.getLogger(__name__)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MITE_SYSLOG_UDP_HOST, MITE_SYSLOG_UDP_PORT))

    log_info(logger, f"[INFO] UDP syslog listener started on {MITE_SYSLOG_UDP_HOST}:{MITE_SYSLOG_UDP_PORT}")

    while True:
        try:
            data, addr = sock.recvfrom(BUFFER_SIZE)
            source_ip = addr[0]
            raw_line = data.decode("utf-8", errors="replace").strip()

            if not raw_line:
                continue

            parsed = parse_syslog_message(raw_line, source_ip=source_ip)
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
            log_error(logger, f"[ERROR] UDP listener error: {e}")


if __name__ == "__main__":
    import time

    logger = logging.getLogger(__name__)
    log_info(logger, "[INFO] UDP listener process starting, waiting 5 seconds...")
    time.sleep(5)

    from src.core.db import init_database
    init_database()

    run_udp_listener()
