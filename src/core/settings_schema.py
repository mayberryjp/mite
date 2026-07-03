"""Canonical schema and defaults for user-editable and read-only settings.

Single source of truth shared by the settings API (validation + metadata) and
the database initializer (seeding default rows). Keeping this in the core layer
avoids the API layer and the DB layer each hard-coding their own copy of the
setting defaults.
"""

from src.core.models import DEFAULT_AI_CUSTOM_TOKENS, DEFAULT_AI_PROMPT_TEMPLATE

EDITABLE_SETTINGS = {
    "ai_prompt_template": {
        "description": "Prompt template sent to the AI for log pattern classification. Use {patterns} as the placeholder for pattern data.",
        "default": DEFAULT_AI_PROMPT_TEMPLATE,
        "type": "string",
        "allow_empty": False,
    },
    "ai_custom_tokens": {
        "description": 'User-managed tokenization rules applied in order using regex substitution. JSON array of ["regex_pattern", "TOKEN_NAME"] pairs. Example: [["\\\\b(?:\\\\d{1,3}\\\\.){3}\\\\d{1,3}\\\\b", "IP_ADDRESS"], ["firewall\\\\.office\\\\.example\\\\.com", "FIREWALL_HOST"]]',
        "default": DEFAULT_AI_CUSTOM_TOKENS,
        "type": "json_list_of_pairs",
        "allow_empty": True,
    },
    "min_message_length": {
        "description": "Minimum log message length required before the processor treats a message as meaningful.",
        "default": "35",
        "type": "int",
        "min": 0,
    },
    "discord_notifications_enabled": {
        "description": "Enable or disable Discord alert notifications.",
        "default": "false",
        "type": "bool",
    },
    "discord_webhook_url": {
        "description": "Discord webhook URL used when Discord notifications are enabled.",
        "default": "",
        "type": "string",
        "allow_empty": True,
    },
    "action_on_new_patterns": {
        "description": "Create an action when a new pattern is discovered.",
        "default": "true",
        "type": "bool",
    },
    "notify_on_new_patterns": {
        "description": "Send a Discord notification when a new pattern is discovered.",
        "default": "false",
        "type": "bool",
    },
    "action_on_no_logs": {
        "description": "Create an action when no logs were received in the last 24 hours.",
        "default": "true",
        "type": "bool",
    },
    "notify_on_no_logs": {
        "description": "Send a Discord notification when no logs were received in the last 24 hours.",
        "default": "false",
        "type": "bool",
    },
    "log_retention_days": {
        "description": "How many days of logs to retain before cleanup.",
        "default": "14",
        "type": "int",
        "min": 1,
    },
    "alert_retention_days": {
        "description": "How many days of alerts to retain before cleanup.",
        "default": "30",
        "type": "int",
        "min": 1,
    },
    "ai_api_daily_rate_limit": {
        "description": "Maximum number of AI API classification calls allowed in a rolling 24-hour window.",
        "default": "500",
        "type": "int",
        "min": 1,
    },
    "ai_discovery_interval_seconds": {
        "description": "How often the AI worker polls pending patterns.",
        "default": "3600",
        "type": "int",
        "min": 1,
    },
    "ai_batch_size": {
        "description": "Number of pending patterns to classify per AI worker cycle.",
        "default": "20",
        "type": "int",
        "min": 1,
    },
    "ai_regex_review_interval_seconds": {
        "description": "How often the AI worker reviews regex duplication/similarity for consolidation suggestions.",
        "default": "604800",
        "type": "int",
        "min": 3600,
    },
    "processor_interval_seconds": {
        "description": "How often the processor runs each cycle.",
        "default": "10",
        "type": "int",
        "min": 1,
    },
    "processor_fetch_limit": {
        "description": "Maximum unprocessed logs fetched by the processor per cycle.",
        "default": "100",
        "type": "int",
        "min": 1,
    },
    "retention_check_interval_seconds": {
        "description": "How often the retention worker runs cleanup.",
        "default": "3600",
        "type": "int",
        "min": 1,
    },
    "udp_batch_size": {
        "description": "UDP listener flush batch size.",
        "default": "500",
        "type": "int",
        "min": 1,
    },
    "udp_batch_flush_interval_seconds": {
        "description": "UDP listener flush interval in seconds.",
        "default": "1.0",
        "type": "float",
        "min": 0.1,
    },
    "udp_recv_buffer_bytes": {
        "description": "Requested UDP socket receive buffer size in bytes.",
        "default": "4194304",
        "type": "int",
        "min": 65536,
    },
    "tcp_batch_size": {
        "description": "TCP listener flush batch size per connection.",
        "default": "500",
        "type": "int",
        "min": 1,
    },
    "tcp_batch_flush_interval_seconds": {
        "description": "TCP listener flush interval in seconds.",
        "default": "1.0",
        "type": "float",
        "min": 0.1,
    },
    "regex_cache_ttl_seconds": {
        "description": "How long processor regex cache is kept before refresh.",
        "default": "60",
        "type": "int",
        "min": 1,
    },
    "write_application_log": {
        "description": "Write application logs to daily files under applogs when enabled.",
        "default": "false",
        "type": "bool",
    },
    "write_syslog_log": {
        "description": "Write inbound non-noise syslogs to daily files under syslogs when enabled.",
        "default": "false",
        "type": "bool",
    },
    "log_ai_requests": {
        "description": "Log every AI request (full request and full response) to its own file under the airequests folder when enabled.",
        "default": "false",
        "type": "bool",
    },
    "syslog_forward_enabled": {
        "description": "Enable forwarding of syslog messages to another destination via UDP.",
        "default": "false",
        "type": "bool",
    },
    "syslog_forward_destination": {
        "description": "Destination for syslog forwarding in format 'host:port' (e.g., '192.168.1.10:514'). Only used when syslog_forward_enabled is true.",
        "default": "",
        "type": "string",
        "allow_empty": True,
    },
    "syslog_forward_min_classification": {
        "description": "Minimum log classification level to forward: 'noise', 'low', 'medium', 'high', or 'critical'. Only logs at this level or higher will be forwarded. Only used when syslog_forward_enabled is true.",
        "default": "low",
        "type": "syslog_classification",
    },
    "db_store_min_classification": {
        "description": "Minimum effective classification level to store logs in the database: 'noise', 'low', 'medium', 'high', or 'critical'. Logs below this level are discarded instead of stored.",
        "default": "low",
        "type": "syslog_classification",
    },
    "write_syslog_min_classification": {
        "description": "Minimum effective classification level to write to the daily syslog files: 'noise', 'low', 'medium', 'high', or 'critical'. Only used when write_syslog_log is enabled.",
        "default": "low",
        "type": "syslog_classification",
    },
}

READ_ONLY_SETTINGS = {
    "ai_efficiency_score": {
        "description": "AI-provided regex efficiency score (0-100) based on duplicate/similar pattern review.",
        "default": 0.0,
        "type": "float",
    },
    "silently_dropped_count": {
        "description": "Running total of logs silently dropped at the listener because they matched a filter-at-listener pattern.",
        "default": 0,
        "type": "int",
    },
    "discarded_too_small_count": {
        "description": "Running total of logs dropped at the listener for being too small / low-signal (below min_message_length or too few real words).",
        "default": 0,
        "type": "int",
    },
    "mite_db_size_bytes": {
        "description": "Current size on disk of the main database file (mite.db: patterns, alerts, stats, settings) in bytes.",
        "default": 0,
        "type": "int",
    },
    "logs_db_size_bytes": {
        "description": "Current size on disk of the logs database file (logs.db) in bytes.",
        "default": 0,
        "type": "int",
    },
}

# Settings seeded on init that are neither user-editable nor surfaced as
# read-only API values (internal worker bookkeeping).
INTERNAL_SETTING_DEFAULTS = {
    "ai_regex_review_last_run_epoch": "0",
}

# Read-only settings that must exist as persisted rows. The *_size_bytes metrics
# are computed on demand at read time and are intentionally not seeded.
_SEEDED_READ_ONLY_KEYS = (
    "ai_efficiency_score",
    "silently_dropped_count",
    "discarded_too_small_count",
)


def default_settings_seed():
    """Return (key, value) tuples for INSERT OR IGNORE seeding of the settings table.

    Values are stringified because the settings table stores TEXT values.
    """
    seed = [(key, str(meta["default"])) for key, meta in EDITABLE_SETTINGS.items()]
    seed += [
        (key, str(READ_ONLY_SETTINGS[key]["default"])) for key in _SEEDED_READ_ONLY_KEYS
    ]
    seed += list(INTERNAL_SETTING_DEFAULTS.items())
    return seed
