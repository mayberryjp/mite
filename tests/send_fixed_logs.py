#!/usr/bin/env python3
"""Send two fixed syslog lines a configurable number of times each over UDP.

Sends each of two hardcoded log lines to a Mite UDP listener. Defaults to
10000 sends per line to 192.168.4.4:1514.

Usage:
    python tests/send_fixed_logs.py
    python tests/send_fixed_logs.py --count 5000 --host 192.168.4.4 --port 1514
"""

import argparse
import socket
import time

DEFAULT_COUNT = 10000
DEFAULT_HOST = "192.168.4.4"
DEFAULT_PORT = 1514

LOG_LINES = [
    "Jun 20 07:33:58 quant sshd-session[2923744]: Connection closed by 192.168.5.1 port 47546",
    'Jun 30 06:42:33 firewall.farm.mayberry.farm nginx: 10.3.10.2 - admin [30/Jun/2026:06:42:33 +0900] "POST /xmlrpc.php HTTP/1.1" 200 15671 "-" "Python-xmlrpc/3.14"',
]


def parse_args():
    parser = argparse.ArgumentParser(description="Send fixed logs over UDP.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="Number of times to send each line.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Destination host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Destination UDP port.")
    parser.add_argument("--delay", type=float, default=0.01, help="Seconds to sleep between sends.")
    return parser.parse_args()


def main():
    args = parse_args()
    total = args.count * len(LOG_LINES)
    print(f"Sending {len(LOG_LINES)} lines x {args.count} = {total} logs to {args.host}:{args.port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0
    try:
        for line in LOG_LINES:
            payload = line.encode("utf-8")
            for _ in range(args.count):
                sock.sendto(payload, (args.host, args.port))
                sent += 1
                if args.delay:
                    time.sleep(args.delay)
                if sent % 1000 == 0:
                    print(f"  sent {sent}/{total}")
    finally:
        sock.close()

    print(f"Done. Sent {sent} logs to {args.host}:{args.port}.")


if __name__ == "__main__":
    main()
