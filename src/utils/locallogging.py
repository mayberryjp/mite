import os
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime

from src.core.config import MITE_DB_PATH, MITE_LOGS_DIR

SETTINGS_CACHE_TTL_SECONDS = 60
_settings_cache = {}
_settings_cache_lock = threading.Lock()

# Re-entrancy guard so error logging never recurses into DB writes:
# log_error -> create_action -> execute_with_retry -> log_error -> ...
_log_error_guard = threading.local()


def _settings_db_path():
    return MITE_DB_PATH


def _read_bool_setting(key, default=False):
    now = time.time()
    with _settings_cache_lock:
        cached = _settings_cache.get(key)
        if cached and now - cached["ts"] < SETTINGS_CACHE_TTL_SECONDS:
            return cached["value"]

    value = default
    try:
        conn = sqlite3.connect(_settings_db_path())
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                value = str(row[0]).strip().lower() in ("true", "1", "yes", "on")
        finally:
            conn.close()
    except sqlite3.Error:
        value = default

    with _settings_cache_lock:
        _settings_cache[key] = {"value": value, "ts": now}
    return value


def _write_daily_log_line(subfolder, message):
    log_dir = os.path.join(MITE_LOGS_DIR, subfolder)
    os.makedirs(log_dir, exist_ok=True)
    log_filename = datetime.now().strftime("%Y-%m-%d.log")
    log_path = os.path.join(log_dir, log_filename)
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(message + "\n")


def write_application_daily_log(logger, message):
    """Write application logs to daily files under applogs when enabled."""
    try:
        if _read_bool_setting("write_application_log", default=False):
            _write_daily_log_line("applogs", message)
    except Exception as e:
        warn = f"[WARN] Failed to write application daily log file: {e}"
        print(warn)
        logger.warning(warn)


def write_syslog_daily_log(logger, message):
    """Write non-noise inbound syslog lines to daily files under syslogs when enabled."""
    try:
        if _read_bool_setting("write_syslog_log", default=False):
            _write_daily_log_line("syslogs", message)
    except Exception as e:
        warn = f"[WARN] Failed to write syslog daily log file: {e}"
        print(warn)
        logger.warning(warn)


def log_info(logger, message):
    """Log a message and print it to the console with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    script_name = os.path.basename(sys.argv[0])
    formatted_message = f"[{timestamp}] {script_name} {message}"
    print(formatted_message)
    logger.info(formatted_message)
    write_application_daily_log(logger, formatted_message)


def log_error(logger, message):
    """
    Log an error message and optionally report it to the cloud API, excluding specified messages.
    Also writes to the daily log file if enabled.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    script_name = os.path.basename(sys.argv[0])
    tb = traceback.extract_tb(sys.exc_info()[2])
    if tb:
        last_frame = tb[-1]
        file_name = os.path.basename(last_frame.filename)
        line_number = last_frame.lineno
    else:
        file_name = script_name
        line_number = "N/A"
    formatted_message = (
        f"[{timestamp}] {script_name}[/{file_name}/{line_number}] {message}"
    )
    print(formatted_message)
    logger.error(formatted_message)
    write_application_daily_log(logger, formatted_message)

    # Guard against re-entrancy: create_action issues DB writes with retries that
    # call log_error on contention, which would otherwise recurse indefinitely.
    if getattr(_log_error_guard, "active", False):
        return
    _log_error_guard.active = True
    try:
        from src.core.db import create_action

        create_action(formatted_message)
    except Exception as e:
        warn = f"[WARN] Failed to record error action: {e}"
        print(warn)
        logger.warning(warn)
    finally:
        _log_error_guard.active = False


def log_warn(logger, message):
    """Log a warning message and print it to the console with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    script_name = os.path.basename(sys.argv[0])
    formatted_message = f"[{timestamp}] {script_name} {message}"
    print(formatted_message)
    logger.warning(formatted_message)
    write_application_daily_log(logger, formatted_message)
