import logging

from src.core.config import ALERT_RETENTION_DAYS, LOG_RETENTION_DAYS
from src.core.db import (
    create_action,
    delete_old_ai_api_calls,
    delete_old_alerts,
    delete_old_dropped_stats,
    delete_old_logs,
    delete_old_noise_stats,
    delete_old_pattern_stats,
    get_actions,
    get_hourly_log_counts,
    get_setting,
)
from src.core.discord import send_discord_message
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)


def _get_retention_days(setting_key, default_days):
    raw_value = get_setting(setting_key, str(default_days))
    try:
        parsed = int(raw_value)
        if parsed < 1:
            raise ValueError(f"{setting_key} must be >= 1")
        return parsed
    except (TypeError, ValueError):
        log_error(
            logger,
            f"[ERROR] Invalid setting '{setting_key}' value '{raw_value}', using default {default_days}",
        )
        return default_days


def _is_setting_enabled(key, default="false"):
    raw_value = get_setting(key, default)
    return str(raw_value).strip().lower() in ("true", "1", "yes", "on")


def _handle_no_logs_last_24h():
    from datetime import datetime

    hourly_stats = get_hourly_log_counts(hours=24)
    total = sum(int(b.get("count", 0) or 0) for b in hourly_stats)
    if total > 0:
        return

    action_enabled = _is_setting_enabled("action_on_no_logs", default="true")
    notify_enabled = _is_setting_enabled("notify_on_no_logs")
    if not action_enabled and not notify_enabled:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    action_text = (
        f"No logs received in the last 24 hours ({today}). "
        "Check syslog sources, transport, and listener health."
    )

    existing_actions, existing_total = get_actions(
        limit=1, offset=0, search=action_text
    )
    if existing_total > 0 or existing_actions:
        return

    if action_enabled:
        create_action(action_text, acknowledged=False)
        log_info(
            logger,
            "[INFO] Retention: created no-logs-24h action",
        )

    if notify_enabled:
        content = (
            "Mite No Logs Detected\n\n"
            "No logs were received in the last 24 hours.\n"
            "Check syslog sources, transport, and listener health."
        )
        send_discord_message(content)


def run_retention():
    log_retention_days = _get_retention_days("log_retention_days", LOG_RETENTION_DAYS)
    alert_retention_days = _get_retention_days(
        "alert_retention_days", ALERT_RETENTION_DAYS
    )

    deleted_logs = delete_old_logs(log_retention_days)
    log_info(
        logger,
        f"[INFO] Retention: deleted {deleted_logs} logs older than {log_retention_days} days",
    )

    deleted_alerts = delete_old_alerts(alert_retention_days)
    log_info(
        logger,
        f"[INFO] Retention: deleted {deleted_alerts} alerts older than {alert_retention_days} days",
    )

    deleted_stats = delete_old_pattern_stats(hours=100)
    log_info(
        logger,
        f"[INFO] Retention: deleted {deleted_stats} pattern stats older than 100 hours",
    )

    deleted_ai_calls = delete_old_ai_api_calls(days=2)
    log_info(
        logger,
        f"[INFO] Retention: deleted {deleted_ai_calls} AI API call records older than 2 days",
    )

    deleted_noise_stats = delete_old_noise_stats(hours=100)
    log_info(
        logger,
        f"[INFO] Retention: deleted {deleted_noise_stats} noise stats older than 100 hours",
    )

    deleted_dropped_stats = delete_old_dropped_stats(hours=100)
    log_info(
        logger,
        f"[INFO] Retention: deleted {deleted_dropped_stats} dropped stats older than 100 hours",
    )

    _handle_no_logs_last_24h()

    return deleted_logs, deleted_alerts
