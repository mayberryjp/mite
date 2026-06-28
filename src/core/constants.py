"""Application-wide constants.

Centralized configuration of magic numbers and string constants used throughout Mite.
Reduces duplication and makes tuning easier.
"""

# Network — UDP/TCP syslog listeners
SYSLOG_UDP_PORT = 1514
SYSLOG_TCP_PORT = 1515
SYSLOG_BUFFER_SIZE = 65535  # Max UDP datagram size
SYSLOG_TCP_RECV_BUFFER_SIZE = 4 * 1024 * 1024  # 4 MB for TCP receive buffer
SYSLOG_TCP_LISTEN_BACKLOG = 50  # TCP listen queue depth

# Batching — Default settings for log batch accumulation
DEFAULT_UDP_BATCH_SIZE = 500
DEFAULT_UDP_BATCH_FLUSH_INTERVAL_SECONDS = 1.0
DEFAULT_TCP_BATCH_SIZE = 500
DEFAULT_TCP_BATCH_FLUSH_INTERVAL_SECONDS = 1.0

# Processing — Processor worker settings
DEFAULT_PROCESSOR_INTERVAL_SECONDS = 10
DEFAULT_PROCESSOR_FETCH_LIMIT = 100
MIN_MESSAGE_LENGTH = 50

# Pattern filtering — Listener-level filter caching
FILTER_CACHE_TTL_SECONDS = 60

# AI Classification — AI worker settings
DEFAULT_AI_BATCH_SIZE = 20
DEFAULT_AI_DISCOVERY_INTERVAL_SECONDS = 3600  # 1 hour
DEFAULT_AI_REGEX_REVIEW_INTERVAL_SECONDS = 7 * 24 * 60 * 60  # 7 days
MAX_AI_REGEX_ATTEMPTS = 3

# Syslog Forwarding
SYSLOG_FORWARD_MIN_CLASSIFICATION = "low"
