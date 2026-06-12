import logging
import os
import sqlite3
import time

from src.core.config import MITE_DB_PATH
from src.core.models import (
    CONST_CREATE_ALERTS_SQL,
    CONST_CREATE_AI_ANALYSES_SQL,
    CONST_CREATE_HOSTS_SQL,
    CONST_CREATE_LOGS_SQL,
    CONST_CREATE_RULE_COOLDOWNS_SQL,
)
from src.utils.locallogging import log_error, log_info

MAX_RETRIES = 5
RETRY_BACKOFF = 0.5


def connect_to_db():
    logger = logging.getLogger(__name__)
    if not os.path.exists(os.path.dirname(MITE_DB_PATH)):
        os.makedirs(os.path.dirname(MITE_DB_PATH), exist_ok=True)
    try:
        conn = sqlite3.connect(MITE_DB_PATH)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except sqlite3.Error as e:
        log_error(logger, f"[ERROR] Error connecting to database {MITE_DB_PATH}: {e}")
        return None


def disconnect_from_db(conn):
    logger = logging.getLogger(__name__)
    try:
        if conn:
            conn.close()
    except sqlite3.Error as e:
        log_error(logger, f"[ERROR] Error closing database connection: {e}")


def init_database():
    logger = logging.getLogger(__name__)
    conn = None
    try:
        conn = sqlite3.connect(MITE_DB_PATH)
        conn.execute("PRAGMA busy_timeout = 10000")
        cursor = conn.cursor()
        for sql in [
            CONST_CREATE_LOGS_SQL,
            CONST_CREATE_ALERTS_SQL,
            CONST_CREATE_HOSTS_SQL,
            CONST_CREATE_RULE_COOLDOWNS_SQL,
            CONST_CREATE_AI_ANALYSES_SQL,
        ]:
            cursor.executescript(sql)
        cursor.execute("PRAGMA journal_mode=WAL;")
        log_info(logger, f"[INFO] Database initialized successfully at {MITE_DB_PATH}")
    except sqlite3.Error as e:
        log_error(logger, f"[ERROR] Error initializing database: {e}")
    finally:
        if conn:
            disconnect_from_db(conn)


def execute_with_retry(func, *args, **kwargs):
    logger = logging.getLogger(__name__)
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF * (2 ** attempt)
                log_error(logger, f"[ERROR] Database locked, retrying in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
            else:
                raise


# --- Log operations ---

def insert_log(received_at, source_ip, host, facility, severity, program, pid, message, raw_message):
    def _insert():
        conn = connect_to_db()
        if not conn:
            return None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO logs (received_at, source_ip, host, facility, severity, program, pid, message, raw_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (received_at, source_ip, host, facility, severity, program, pid, message, raw_message),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            disconnect_from_db(conn)

    return execute_with_retry(_insert)


def get_unprocessed_logs(limit=500):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, received_at, source_ip, host, facility, severity, program, pid, message, raw_message FROM logs WHERE processed = 0 ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0], "received_at": r[1], "source_ip": r[2], "host": r[3],
                "facility": r[4], "severity": r[5], "program": r[6], "pid": r[7],
                "message": r[8], "raw_message": r[9],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


def mark_logs_processed(log_ids):
    if not log_ids:
        return
    conn = connect_to_db()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in log_ids)
        cursor.execute(f"UPDATE logs SET processed = 1 WHERE id IN ({placeholders})", log_ids)
        conn.commit()
    finally:
        disconnect_from_db(conn)


def mark_log_ai_candidate(log_id):
    conn = connect_to_db()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE logs SET ai_candidate = 1 WHERE id = ?", (log_id,))
        conn.commit()
    finally:
        disconnect_from_db(conn)


def get_logs(limit=100, offset=0, host=None, source_ip=None, program=None,
             severity=None, search=None, start=None, end=None):
    conn = connect_to_db()
    if not conn:
        return [], 0
    try:
        cursor = conn.cursor()
        conditions = []
        params = []

        if host:
            conditions.append("host = ?")
            params.append(host)
        if source_ip:
            conditions.append("source_ip = ?")
            params.append(source_ip)
        if program:
            conditions.append("program = ?")
            params.append(program)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        if search:
            conditions.append("message LIKE ?")
            params.append(f"%{search}%")
        if start:
            conditions.append("received_at >= ?")
            params.append(start)
        if end:
            conditions.append("received_at <= ?")
            params.append(end)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        cursor.execute(f"SELECT COUNT(*) FROM logs {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(
            f"SELECT id, received_at, source_ip, host, facility, severity, program, pid, message FROM logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = cursor.fetchall()
        items = [
            {
                "id": r[0], "received_at": r[1], "source_ip": r[2], "host": r[3],
                "facility": r[4], "severity": r[5], "program": r[6], "pid": r[7],
                "message": r[8],
            }
            for r in rows
        ]
        return items, total
    finally:
        disconnect_from_db(conn)


def get_recent_logs(after_id=0, limit=50):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, received_at, source_ip, host, facility, severity, program, pid, message FROM logs WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after_id, limit),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0], "received_at": r[1], "source_ip": r[2], "host": r[3],
                "facility": r[4], "severity": r[5], "program": r[6], "pid": r[7],
                "message": r[8],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


def get_log_samples_for_source(source_ip=None, host=None, limit=100):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        conditions = []
        params = []
        if source_ip:
            conditions.append("source_ip = ?")
            params.append(source_ip)
        if host:
            conditions.append("host = ?")
            params.append(host)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        cursor.execute(
            f"SELECT DISTINCT message FROM logs {where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        )
        return [r[0] for r in cursor.fetchall()]
    finally:
        disconnect_from_db(conn)


# --- Alert operations ---

def insert_alert(created_at, log_id, rule_name, severity, host, source_ip, message, reason, action):
    def _insert():
        conn = connect_to_db()
        if not conn:
            return None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO alerts (created_at, log_id, rule_name, severity, host, source_ip, message, reason, action)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (created_at, log_id, rule_name, severity, host, source_ip, message, reason, action),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            disconnect_from_db(conn)

    return execute_with_retry(_insert)


def update_alert_discord_sent(alert_id):
    conn = connect_to_db()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE alerts SET discord_sent = 1 WHERE id = ?", (alert_id,))
        conn.commit()
    finally:
        disconnect_from_db(conn)


def get_alerts(limit=100, offset=0, severity=None, host=None, source_ip=None,
               rule_name=None, search=None):
    conn = connect_to_db()
    if not conn:
        return [], 0
    try:
        cursor = conn.cursor()
        conditions = []
        params = []

        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        if host:
            conditions.append("host = ?")
            params.append(host)
        if source_ip:
            conditions.append("source_ip = ?")
            params.append(source_ip)
        if rule_name:
            conditions.append("rule_name = ?")
            params.append(rule_name)
        if search:
            conditions.append("message LIKE ?")
            params.append(f"%{search}%")

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        cursor.execute(f"SELECT COUNT(*) FROM alerts {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(
            f"SELECT id, created_at, log_id, rule_name, severity, host, source_ip, message, reason, action, discord_sent FROM alerts {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = cursor.fetchall()
        items = [
            {
                "id": r[0], "created_at": r[1], "log_id": r[2], "rule_name": r[3],
                "severity": r[4], "host": r[5], "source_ip": r[6], "message": r[7],
                "reason": r[8], "action": r[9], "discord_sent": bool(r[10]),
            }
            for r in rows
        ]
        return items, total
    finally:
        disconnect_from_db(conn)


# --- Host operations ---

def upsert_host(host, source_ip, timestamp):
    def _upsert():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO hosts (host, source_ip, first_seen_at, last_seen_at, log_count)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(source_ip, host)
                   DO UPDATE SET last_seen_at = ?, log_count = log_count + 1""",
                (host, source_ip, timestamp, timestamp, timestamp),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_upsert)


def increment_host_alert_count(host, source_ip):
    conn = connect_to_db()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE hosts SET alert_count = alert_count + 1 WHERE source_ip = ? AND (host = ? OR host IS NULL)",
            (source_ip, host),
        )
        conn.commit()
    finally:
        disconnect_from_db(conn)


def get_all_hosts():
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, host, source_ip, first_seen_at, last_seen_at, log_count, alert_count FROM hosts ORDER BY last_seen_at DESC"
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0], "host": r[1], "source_ip": r[2],
                "first_seen_at": r[3], "last_seen_at": r[4],
                "log_count": r[5], "alert_count": r[6],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


# --- Cooldown operations ---

def check_cooldown(rule_name, cooldown_key, cooldown_seconds):
    conn = connect_to_db()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_sent_at FROM rule_cooldowns WHERE rule_name = ? AND cooldown_key = ?",
            (rule_name, cooldown_key),
        )
        row = cursor.fetchone()
        if not row:
            return False
        from datetime import datetime, timedelta
        last_sent = datetime.fromisoformat(row[0])
        return (datetime.now() - last_sent).total_seconds() < cooldown_seconds
    finally:
        disconnect_from_db(conn)


def update_cooldown(rule_name, cooldown_key):
    from datetime import datetime
    conn = connect_to_db()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            """INSERT INTO rule_cooldowns (rule_name, cooldown_key, last_sent_at)
               VALUES (?, ?, ?)
               ON CONFLICT(rule_name, cooldown_key)
               DO UPDATE SET last_sent_at = ?""",
            (rule_name, cooldown_key, now, now),
        )
        conn.commit()
    finally:
        disconnect_from_db(conn)


# --- AI analyses operations ---

def insert_ai_analysis(created_at, source_ip, host, sample_count, markdown_path, status, summary=None):
    conn = connect_to_db()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO ai_analyses (created_at, source_ip, host, sample_count, markdown_path, status, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (created_at, source_ip, host, sample_count, markdown_path, status, summary),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        disconnect_from_db(conn)


def get_ai_analyses():
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, created_at, source_ip, host, sample_count, markdown_path, status, summary FROM ai_analyses ORDER BY id DESC"
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0], "created_at": r[1], "source_ip": r[2], "host": r[3],
                "sample_count": r[4], "markdown_path": r[5], "status": r[6], "summary": r[7],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


def get_ai_analysis_by_id(analysis_id):
    conn = connect_to_db()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, created_at, source_ip, host, sample_count, markdown_path, status, summary FROM ai_analyses WHERE id = ?",
            (analysis_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "created_at": row[1], "source_ip": row[2], "host": row[3],
            "sample_count": row[4], "markdown_path": row[5], "status": row[6], "summary": row[7],
        }
    finally:
        disconnect_from_db(conn)


def get_unanalyzed_sources(min_count=25):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT h.source_ip, h.host, h.log_count
               FROM hosts h
               LEFT JOIN ai_analyses a ON h.source_ip = a.source_ip
               WHERE a.id IS NULL AND h.log_count >= ?
               ORDER BY h.log_count DESC""",
            (min_count,),
        )
        rows = cursor.fetchall()
        return [{"source_ip": r[0], "host": r[1], "log_count": r[2]} for r in rows]
    finally:
        disconnect_from_db(conn)


# --- Stats operations ---

def get_stats():
    conn = connect_to_db()
    if not conn:
        return {}
    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM logs WHERE received_at >= datetime('now', '-1 hour')"
        )
        logs_last_hour = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM logs WHERE received_at >= datetime('now', '-24 hours')"
        )
        logs_last_24h = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM alerts WHERE created_at >= datetime('now', '-1 hour')"
        )
        alerts_last_hour = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM alerts WHERE created_at >= datetime('now', '-24 hours')"
        )
        alerts_last_24h = cursor.fetchone()[0]

        cursor.execute(
            "SELECT source_ip, host, log_count FROM hosts ORDER BY log_count DESC LIMIT 10"
        )
        top_hosts = [{"source_ip": r[0], "host": r[1], "log_count": r[2]} for r in cursor.fetchall()]

        cursor.execute(
            "SELECT rule_name, COUNT(*) as cnt FROM alerts GROUP BY rule_name ORDER BY cnt DESC LIMIT 10"
        )
        top_rules = [{"rule_name": r[0], "count": r[1]} for r in cursor.fetchall()]

        db_size = 0
        if os.path.exists(MITE_DB_PATH):
            db_size = os.path.getsize(MITE_DB_PATH)

        from src.core.config import AI_DISCOVERY_ENABLED
        return {
            "logs_last_hour": logs_last_hour,
            "logs_last_24h": logs_last_24h,
            "alerts_last_hour": alerts_last_hour,
            "alerts_last_24h": alerts_last_24h,
            "top_hosts": top_hosts,
            "top_alert_rules": top_rules,
            "database_size_bytes": db_size,
            "ai_enabled": AI_DISCOVERY_ENABLED,
        }
    finally:
        disconnect_from_db(conn)


# --- Retention operations ---

def delete_old_logs(days):
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM logs WHERE received_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_old_alerts(days):
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM alerts WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)
