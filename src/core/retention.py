import logging

from src.core.config import LOG_RETENTION_DAYS, ALERT_RETENTION_DAYS
from src.core.db import delete_old_logs, delete_old_alerts, delete_old_pattern_stats, delete_old_ai_api_calls
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)


def run_retention():
    deleted_logs = delete_old_logs(LOG_RETENTION_DAYS)
    log_info(logger, f"[INFO] Retention: deleted {deleted_logs} logs older than {LOG_RETENTION_DAYS} days")

    deleted_alerts = delete_old_alerts(ALERT_RETENTION_DAYS)
    log_info(logger, f"[INFO] Retention: deleted {deleted_alerts} alerts older than {ALERT_RETENTION_DAYS} days")

    deleted_stats = delete_old_pattern_stats(hours=100)
    log_info(logger, f"[INFO] Retention: deleted {deleted_stats} pattern stats older than 100 hours")

    deleted_ai_calls = delete_old_ai_api_calls(days=2)
    log_info(logger, f"[INFO] Retention: deleted {deleted_ai_calls} AI API call records older than 2 days")

    return deleted_logs, deleted_alerts
