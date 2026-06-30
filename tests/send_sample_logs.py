#!/usr/bin/env python3
"""Send a randomized batch of sample syslog lines to a Mite UDP listener.

Reads every line from the .log files under tests/samples, picks a configurable
number of lines at random (with replacement), and sends each over UDP.

Usage:
    python tests/send_sample_logs.py
    python tests/send_sample_logs.py --count 5000 --host 192.168.4.4 --port 1514
"""

import argparse
import os
import random
import socket
import time

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")

DEFAULT_COUNT = 1000
DEFAULT_HOST = "192.168.4.4"
DEFAULT_PORT = 1514
# Throttle to roughly 10000 logs/hour (3600s / 10000 = 0.36s per log).
DEFAULT_DELAY = 0.36


def load_sample_lines(samples_dir):
    """Read all non-empty lines from every .log file in samples_dir."""
    lines = []
    for name in sorted(os.listdir(samples_dir)):
        if not name.endswith(".log"):
            continue
        path = os.path.join(samples_dir, name)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    return lines


def parse_args():
    parser = argparse.ArgumentParser(description="Send randomized sample logs over UDP.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="Number of logs to send.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Destination host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Destination UDP port.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Seconds to sleep between sends (default ~10000 logs/hour).")
    parser.add_argument("--seed", type=int, default=None, help="Optional random seed for reproducibility.")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    lines = load_sample_lines(SAMPLES_DIR)
    if not lines:
        raise SystemExit(f"No sample log lines found in {SAMPLES_DIR}")

    print(f"Loaded {len(lines)} sample lines; sending {args.count} to {args.host}:{args.port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for i in range(1, args.count + 1):
            payload = random.choice(lines).encode("utf-8")
            sock.sendto(payload, (args.host, args.port))
            if args.delay:
                time.sleep(args.delay)
            if i % 100 == 0:
                print(f"  sent {i}/{args.count}")
    finally:
        sock.close()

    print(f"Done. Sent {args.count} logs to {args.host}:{args.port}.")


if __name__ == "__main__":
    main()
