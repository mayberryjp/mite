import logging

from src.core.config import LOG_RETENTION_DAYS, ALERT_RETENTION_DAYS
from src.core.db import delete_old_logs, delete_old_alerts, delete_old_pattern_stats, delete_old_ai_api_calls, delete_old_noise_stats, get_setting
from src.utils.locallogging import log_info, log_error

logger = logging.getLogger(__name__)


def _get_retention_days(setting_key, default_days):
    raw_value = get_setting(setting_key, str(default_days))
    try:
        parsed = int(raw_value)
        if parsed < 1:
            raise ValueError(f"{setting_key} must be >= 1")
        return parsed
    except (TypeError, ValueError):
        log_error(logger, f"[ERROR] Invalid setting '{setting_key}' value '{raw_value}', using default {default_days}")
        return default_days


def run_retention():
    log_retention_days = _get_retention_days("log_retention_days", LOG_RETENTION_DAYS)
    alert_retention_days = _get_retention_days("alert_retention_days", ALERT_RETENTION_DAYS)

    deleted_logs = delete_old_logs(log_retention_days)
    log_info(logger, f"[INFO] Retention: deleted {deleted_logs} logs older than {log_retention_days} days")

    deleted_alerts = delete_old_alerts(alert_retention_days)
    log_info(logger, f"[INFO] Retention: deleted {deleted_alerts} alerts older than {alert_retention_days} days")

    deleted_stats = delete_old_pattern_stats(hours=100)
    log_info(logger, f"[INFO] Retention: deleted {deleted_stats} pattern stats older than 100 hours")

    deleted_ai_calls = delete_old_ai_api_calls(days=2)
    log_info(logger, f"[INFO] Retention: deleted {deleted_ai_calls} AI API call records older than 2 days")

    deleted_noise_stats = delete_old_noise_stats(hours=100)
    log_info(logger, f"[INFO] Retention: deleted {deleted_noise_stats} noise stats older than 100 hours")

    return deleted_logs, deleted_alerts
