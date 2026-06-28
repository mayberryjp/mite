"""
Syslog forwarder utility for forwarding logs to remote syslog servers.
"""

import logging
import socket
from datetime import datetime

from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

# Valid classification levels in order of severity (lowest to highest)
CLASSIFICATION_LEVELS = ["noise", "low", "medium", "high", "critical"]


def _parse_destination(destination_str):
    """Parse destination string in format 'host:port' and return (host, port) tuple.

    Returns (host, port) or (None, None) if parsing fails.
    """
    if not destination_str or not isinstance(destination_str, str):
        return None, None

    parts = destination_str.strip().rsplit(":", 1)
    if len(parts) != 2:
        log_error(
            logger,
            f"[ERROR] Invalid syslog destination format: {destination_str}. Expected 'host:port'",
        )
        return None, None

    host = parts[0].strip()
    port_str = parts[1].strip()

    try:
        port = int(port_str)
        if port < 1 or port > 65535:
            raise ValueError("port out of range")
        return host, port
    except (ValueError, TypeError):
        log_error(
            logger,
            f"[ERROR] Invalid port in syslog destination: {port_str}. Must be 1-65535",
        )
        return None, None


def _should_forward_log(log_classification, min_classification):
    """Determine if a log should be forwarded based on classification levels.

    A log is forwarded if its classification level is >= min_classification level.

    Args:
        log_classification: The classification of the log (e.g., 'low', 'high')
        min_classification: The minimum classification level to forward (e.g., 'medium')

    Returns:
        True if the log should be forwarded, False otherwise.
    """
    try:
        log_level = CLASSIFICATION_LEVELS.index(log_classification.lower())
        min_level = CLASSIFICATION_LEVELS.index(min_classification.lower())
        return log_level >= min_level
    except (ValueError, AttributeError):
        # Invalid classification, don't forward
        return False


def _format_syslog_message(message, facility=16, severity=6):
    """Format a message as RFC 3164 syslog format.

    Args:
        message: The message text
        facility: Syslog facility code (default 16 = local0)
        severity: Syslog severity code (default 6 = informational)

    Returns:
        Formatted syslog message string
    """
    # Calculate priority: facility * 8 + severity
    priority = facility * 8 + severity

    # Timestamp in RFC 3164 format: Mmm dd hh:mm:ss
    now = datetime.now()
    timestamp = now.strftime("%b %d %H:%M:%S")

    # Hostname can be omitted or set to local hostname
    hostname = socket.gethostname()

    # Format: <PRI>TIMESTAMP HOSTNAME MSG
    syslog_msg = f"<{priority}>{timestamp} {hostname} mite[forwarder]: {message}"
    return syslog_msg


def forward_log_to_syslog(
    message, destination_str, log_classification, min_classification
):
    """Forward a log message to a remote syslog server via UDP.

    Args:
        message: The log message to forward
        destination_str: Destination in format 'host:port'
        log_classification: The classification of the log
        min_classification: The minimum classification level to forward

    Returns:
        True if forwarding was successful or skipped, False if an error occurred.
    """
    if not message:
        return True

    if not destination_str:
        return True

    # Check if log should be forwarded based on classification
    if not _should_forward_log(log_classification, min_classification):
        return True

    # Parse destination
    host, port = _parse_destination(destination_str)
    if host is None or port is None:
        return False

    try:
        # Format message as RFC 3164 syslog
        syslog_msg = _format_syslog_message(message)

        # Send via UDP
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)  # 2-second timeout

        try:
            sock.sendto(syslog_msg.encode("utf-8"), (host, port))
            log_info(
                logger,
                f"[INFO] Forwarded log to {host}:{port} (classification: {log_classification})",
            )
            return True
        finally:
            sock.close()

    except socket.gaierror as e:
        log_error(logger, f"[ERROR] Failed to resolve syslog destination {host}: {e}")
        return False
    except socket.timeout:
        log_error(
            logger,
            f"[ERROR] Timeout sending syslog to {host}:{port} (2 second timeout)",
        )
        return False
    except Exception as e:
        log_error(
            logger,
            f"[ERROR] Error forwarding syslog to {host}:{port}: {type(e).__name__}: {e}",
        )
        return False
