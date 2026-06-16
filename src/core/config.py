import os
import logging

from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)

# API
MITE_API_HOST = os.getenv("MITE_API_HOST", "0.0.0.0")
MITE_API_PORT = int(os.getenv("MITE_API_PORT", "8080"))

# Syslog
MITE_SYSLOG_UDP_HOST = os.getenv("MITE_SYSLOG_UDP_HOST", "0.0.0.0")
MITE_SYSLOG_UDP_PORT = int(os.getenv("MITE_SYSLOG_UDP_PORT", "1514"))
MITE_SYSLOG_TCP_HOST = os.getenv("MITE_SYSLOG_TCP_HOST", "0.0.0.0")
MITE_SYSLOG_TCP_PORT = int(os.getenv("MITE_SYSLOG_TCP_PORT", "1515"))

# Database
MITE_DB_PATH = os.getenv("MITE_DB_PATH", "/app/data/Mite.sqlite")

# Logs directory
MITE_LOGS_DIR = os.getenv("MITE_LOGS_DIR", "/app/logs")

# AI Discovery
AI_API_BASE_URL = os.getenv("AI_API_BASE_URL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")
AI_DISCOVERY_INTERVAL_SECONDS = int(os.getenv("AI_DISCOVERY_INTERVAL_SECONDS", "3600"))

# Retention
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "14"))
ALERT_RETENTION_DAYS = int(os.getenv("ALERT_RETENTION_DAYS", "30"))

VERSION = "0.1.0"


def get_config_summary():
    return {
        "api_host": MITE_API_HOST,
        "api_port": MITE_API_PORT,
        "syslog_udp_port": MITE_SYSLOG_UDP_PORT,
        "syslog_tcp_port": MITE_SYSLOG_TCP_PORT,
        "db_path": MITE_DB_PATH,
        "discord_configured": False,
        "ai_configured": bool(AI_API_BASE_URL and AI_API_KEY and AI_MODEL),
        "log_retention_days": LOG_RETENTION_DAYS,
        "alert_retention_days": ALERT_RETENTION_DAYS,
        "version": VERSION,
    }


def ensure_directories():
    for d in [
        os.path.dirname(MITE_DB_PATH),
        MITE_LOGS_DIR,
    ]:
        os.makedirs(d, exist_ok=True)
        log_info(logger, f"[INFO] Ensured directory exists: {d}")
