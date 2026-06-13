import logging
import os
import sqlite3
import time

from src.core.config import MITE_DB_PATH
from src.core.models import (
    CONST_CREATE_ALERTS_SQL,
    CONST_CREATE_HOSTS_SQL,
    CONST_CREATE_LOGS_SQL,
    CONST_CREATE_PATTERNS_SQL,
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
            CONST_CREATE_PATTERNS_SQL,
            CONST_CREATE_LOGS_SQL,
            CONST_CREATE_ALERTS_SQL,
            CONST_CREATE_HOSTS_SQL,
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


def mark_logs_processed(log_ids, pattern_id=None):
    if not log_ids:
        return
    def _mark():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in log_ids)
            if pattern_id is not None:
                cursor.execute(
                    f"UPDATE logs SET processed = 1, pattern_id = ? WHERE id IN ({placeholders})",
                    [pattern_id] + list(log_ids),
                )
            else:
                cursor.execute(
                    f"UPDATE logs SET processed = 1 WHERE id IN ({placeholders})",
                    list(log_ids),
                )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_mark)


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
            f"SELECT id, received_at, source_ip, host, facility, severity, program, pid, message, pattern_id FROM logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = cursor.fetchall()
        items = [
            {
                "id": r[0], "received_at": r[1], "source_ip": r[2], "host": r[3],
                "facility": r[4], "severity": r[5], "program": r[6], "pid": r[7],
                "message": r[8], "pattern_id": r[9],
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
            "SELECT id, received_at, source_ip, host, facility, severity, program, pid, message, pattern_id FROM logs WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after_id, limit),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0], "received_at": r[1], "source_ip": r[2], "host": r[3],
                "facility": r[4], "severity": r[5], "program": r[6], "pid": r[7],
                "message": r[8], "pattern_id": r[9],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


# --- Pattern operations ---

def get_pattern_by_hash(pattern_hash):
    conn = connect_to_db()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, pattern_hash, pattern_text, sample_message, classification, ai_explanation, user_override, host, program, hit_count, first_seen_at, last_seen_at FROM patterns WHERE pattern_hash = ?",
            (pattern_hash,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "pattern_hash": row[1], "pattern_text": row[2],
            "sample_message": row[3], "classification": row[4],
            "ai_explanation": row[5], "user_override": row[6],
            "host": row[7], "program": row[8], "hit_count": row[9],
            "first_seen_at": row[10], "last_seen_at": row[11],
        }
    finally:
        disconnect_from_db(conn)


def insert_pattern(pattern_hash, pattern_text, sample_message, host=None, program=None, timestamp=None):
    def _insert():
        conn = connect_to_db()
        if not conn:
            return None
        try:
            cursor = conn.cursor()
            ts = timestamp or ""
            cursor.execute(
                """INSERT INTO patterns (pattern_hash, pattern_text, sample_message, host, program, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pattern_hash, pattern_text, sample_message, host, program, ts, ts),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            disconnect_from_db(conn)

    return execute_with_retry(_insert)


def increment_pattern_hit(pattern_id, timestamp):
    def _update():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE patterns SET hit_count = hit_count + 1, last_seen_at = ? WHERE id = ?",
                (timestamp, pattern_id),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_update)


def get_pending_patterns(limit=50):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, pattern_hash, pattern_text, sample_message, host, program, hit_count, first_seen_at, last_seen_at FROM patterns WHERE classification = 'pending' ORDER BY hit_count DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0], "pattern_hash": r[1], "pattern_text": r[2],
                "sample_message": r[3], "host": r[4], "program": r[5],
                "hit_count": r[6], "first_seen_at": r[7], "last_seen_at": r[8],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


def update_pattern_classification(pattern_id, classification, ai_explanation=None):
    def _update():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE patterns SET classification = ?, ai_explanation = ? WHERE id = ?",
                (classification, ai_explanation, pattern_id),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_update)


def update_pattern_user_override(pattern_id, user_override):
    def _update():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE patterns SET user_override = ? WHERE id = ?",
                (user_override, pattern_id),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_update)


def get_all_patterns(limit=100, offset=0, classification=None):
    conn = connect_to_db()
    if not conn:
        return [], 0
    try:
        cursor = conn.cursor()
        conditions = []
        params = []

        if classification:
            conditions.append("classification = ?")
            params.append(classification)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        cursor.execute(f"SELECT COUNT(*) FROM patterns {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(
            f"""SELECT id, pattern_hash, pattern_text, sample_message, classification,
                       ai_explanation, user_override, host, program, hit_count,
                       first_seen_at, last_seen_at
                FROM patterns {where}
                ORDER BY last_seen_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = cursor.fetchall()
        items = [
            {
                "id": r[0], "pattern_hash": r[1], "pattern_text": r[2],
                "sample_message": r[3], "classification": r[4],
                "ai_explanation": r[5], "user_override": r[6],
                "host": r[7], "program": r[8], "hit_count": r[9],
                "first_seen_at": r[10], "last_seen_at": r[11],
                "effective_classification": r[6] if r[6] else r[4],
            }
            for r in rows
        ]
        return items, total
    finally:
        disconnect_from_db(conn)


def get_pattern_by_id(pattern_id):
    conn = connect_to_db()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, pattern_hash, pattern_text, sample_message, classification,
                      ai_explanation, user_override, host, program, hit_count,
                      first_seen_at, last_seen_at
               FROM patterns WHERE id = ?""",
            (pattern_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "pattern_hash": row[1], "pattern_text": row[2],
            "sample_message": row[3], "classification": row[4],
            "ai_explanation": row[5], "user_override": row[6],
            "host": row[7], "program": row[8], "hit_count": row[9],
            "first_seen_at": row[10], "last_seen_at": row[11],
            "effective_classification": row[6] if row[6] else row[4],
        }
    finally:
        disconnect_from_db(conn)


# --- Alert operations ---

def insert_alert(created_at, log_id, pattern_id, severity, host, source_ip, message, reason, action):
    def _insert():
        conn = connect_to_db()
        if not conn:
            return None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO alerts (created_at, log_id, pattern_id, severity, host, source_ip, message, reason, action)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (created_at, log_id, pattern_id, severity, host, source_ip, message, reason, action),
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
               pattern_id=None, search=None):
    conn = connect_to_db()
    if not conn:
        return [], 0
    try:
        cursor = conn.cursor()
        conditions = []
        params = []

        if severity:
            conditions.append("a.severity = ?")
            params.append(severity)
        if host:
            conditions.append("a.host = ?")
            params.append(host)
        if source_ip:
            conditions.append("a.source_ip = ?")
            params.append(source_ip)
        if pattern_id:
            conditions.append("a.pattern_id = ?")
            params.append(pattern_id)
        if search:
            conditions.append("a.message LIKE ?")
            params.append(f"%{search}%")

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        cursor.execute(f"SELECT COUNT(*) FROM alerts a {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(
            f"""SELECT a.id, a.created_at, a.log_id, a.pattern_id, a.severity, a.host,
                       a.source_ip, a.message, a.reason, a.action, a.discord_sent,
                       p.pattern_text
                FROM alerts a
                LEFT JOIN patterns p ON a.pattern_id = p.id
                {where} ORDER BY a.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = cursor.fetchall()
        items = [
            {
                "id": r[0], "created_at": r[1], "log_id": r[2], "pattern_id": r[3],
                "severity": r[4], "host": r[5], "source_ip": r[6], "message": r[7],
                "reason": r[8], "action": r[9], "discord_sent": bool(r[10]),
                "pattern_text": r[11],
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

        cursor.execute("SELECT COUNT(*) FROM patterns")
        total_patterns = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM patterns WHERE classification = 'pending'")
        pending_patterns = cursor.fetchone()[0]

        cursor.execute(
            "SELECT classification, COUNT(*) as cnt FROM patterns GROUP BY classification ORDER BY cnt DESC"
        )
        pattern_breakdown = [{"classification": r[0], "count": r[1]} for r in cursor.fetchall()]

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
            "total_patterns": total_patterns,
            "pending_patterns": pending_patterns,
            "pattern_breakdown": pattern_breakdown,
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
