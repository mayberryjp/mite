import logging
import os
import sqlite3
import time

from src.core.config import MITE_DB_PATH
from src.core.models import (
    CONST_CREATE_AI_API_CALLS_SQL,
    CONST_CREATE_ALERTS_SQL,
    CONST_CREATE_HOSTS_SQL,
    CONST_CREATE_LOGS_SQL,
    CONST_CREATE_NOISE_STATS_SQL,
    CONST_CREATE_PATTERN_STATS_SQL,
    CONST_CREATE_PATTERNS_SQL,
    CONST_CREATE_SETTINGS_SQL,
    DEFAULT_AI_CUSTOM_TOKENS,
    DEFAULT_AI_PROMPT_TEMPLATE,
    DEFAULT_AI_SAMPLE_PREPROCESSING_REGEX,
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
            CONST_CREATE_PATTERN_STATS_SQL,
            CONST_CREATE_NOISE_STATS_SQL,
            CONST_CREATE_AI_API_CALLS_SQL,
            CONST_CREATE_SETTINGS_SQL,
        ]:
            cursor.executescript(sql)
        cursor.execute("PRAGMA journal_mode=WAL;")
        # Seed/overwrite default prompt on every startup to ensure latest version is active
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("ai_prompt_template", DEFAULT_AI_PROMPT_TEMPLATE),
        )
        # Seed/overwrite sample preprocessing regex to strip dynamic values before AI classification
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("ai_sample_preprocessing_regex", DEFAULT_AI_SAMPLE_PREPROCESSING_REGEX),
        )
        # Seed custom keyword tokens if not already set (user may have customized these)
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("ai_custom_tokens", DEFAULT_AI_CUSTOM_TOKENS),
        )
        # Seed default minimum message length if not already set
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("min_message_length", "50"),
        )
        # Seed Discord notification settings if not already set
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("discord_notifications_enabled", "false"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("discord_webhook_url", ""),
        )
        # Seed retention settings if not already set
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("log_retention_days", "14"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("alert_retention_days", "30"),
        )
        # Seed AI API daily rate limit if not already set
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("ai_api_daily_rate_limit", "500"),
        )
        # Seed worker/runtime tuning settings if not already set
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("ai_discovery_interval_seconds", "3600"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("ai_batch_size", "20"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("processor_interval_seconds", "10"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("processor_fetch_limit", "100"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("retention_check_interval_seconds", "3600"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("udp_batch_size", "500"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("udp_batch_flush_interval_seconds", "1.0"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("udp_recv_buffer_bytes", "4194304"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("tcp_batch_size", "500"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("tcp_batch_flush_interval_seconds", "1.0"),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("regex_cache_ttl_seconds", "60"),
        )
        conn.commit()
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
                wait = RETRY_BACKOFF * (2**attempt)
                log_error(
                    logger,
                    f"[ERROR] Database locked, retrying in {wait}s (attempt {attempt + 1})",
                )
                time.sleep(wait)
            else:
                raise


# --- Log operations ---


def insert_log(
    received_at, source_ip, host, facility, severity, program, pid, message, raw_message
):
    def _insert():
        conn = connect_to_db()
        if not conn:
            return None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO logs (received_at, source_ip, host, facility, severity, program, pid, message, raw_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    received_at,
                    source_ip,
                    host,
                    facility,
                    severity,
                    program,
                    pid,
                    message,
                    raw_message,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            disconnect_from_db(conn)

    return execute_with_retry(_insert)


def insert_logs_batch(logs, conn=None):
    """Insert multiple logs in a single transaction. Each log is a tuple of
    (received_at, source_ip, host, facility, severity, program, pid, message, raw_message).
    If conn is provided, uses it directly without closing it."""
    if not logs:
        return

    own_conn = conn is None
    if own_conn:
        conn = connect_to_db()
        if not conn:
            return

    def _batch_insert():
        cursor = conn.cursor()
        cursor.executemany(
            """INSERT INTO logs (received_at, source_ip, host, facility, severity, program, pid, message, raw_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            logs,
        )
        conn.commit()

    try:
        execute_with_retry(_batch_insert)
    finally:
        if own_conn:
            disconnect_from_db(conn)


def upsert_hosts_batch(hosts, conn=None):
    """Upsert multiple hosts in a single transaction. Each host is a tuple of
    (host, source_ip, timestamp)."""
    if not hosts:
        return

    own_conn = conn is None
    if own_conn:
        conn = connect_to_db()
        if not conn:
            return

    def _batch_upsert():
        cursor = conn.cursor()
        cursor.executemany(
            """INSERT INTO hosts (host, source_ip, first_seen_at, last_seen_at, log_count)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(source_ip, host)
               DO UPDATE SET last_seen_at = ?, log_count = log_count + 1""",
            [(h, s, t, t, t) for h, s, t in hosts],
        )
        conn.commit()

    try:
        execute_with_retry(_batch_upsert)
    finally:
        if own_conn:
            disconnect_from_db(conn)


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
                "id": r[0],
                "received_at": r[1],
                "source_ip": r[2],
                "host": r[3],
                "facility": r[4],
                "severity": r[5],
                "program": r[6],
                "pid": r[7],
                "message": r[8],
                "raw_message": r[9],
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


def get_logs(
    limit=100,
    offset=0,
    host=None,
    source_ip=None,
    program=None,
    severity=None,
    search=None,
    start=None,
    end=None,
):
    conn = connect_to_db()
    if not conn:
        return [], 0
    try:
        cursor = conn.cursor()
        conditions = ["processed = 1"]
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
            conditions.append("datetime(received_at) >= ?")
            params.append(start)
        if end:
            conditions.append("datetime(received_at) <= ?")
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
                "id": r[0],
                "received_at": r[1],
                "source_ip": r[2],
                "host": r[3],
                "facility": r[4],
                "severity": r[5],
                "program": r[6],
                "pid": r[7],
                "message": r[8],
                "pattern_id": r[9],
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
            "SELECT id, received_at, source_ip, host, facility, severity, program, pid, message, pattern_id FROM logs WHERE processed = 1 AND id > ? ORDER BY id DESC LIMIT ?",
            (after_id, limit),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "received_at": r[1],
                "source_ip": r[2],
                "host": r[3],
                "facility": r[4],
                "severity": r[5],
                "program": r[6],
                "pid": r[7],
                "message": r[8],
                "pattern_id": r[9],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


def get_logs_by_pattern(pattern_id, limit=100, offset=0):
    conn = connect_to_db()
    if not conn:
        return [], 0
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM logs WHERE pattern_id = ?", (pattern_id,))
        total = cursor.fetchone()[0]

        cursor.execute(
            "SELECT id, received_at, source_ip, host, facility, severity, program, pid, message, pattern_id FROM logs WHERE pattern_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (pattern_id, limit, offset),
        )
        rows = cursor.fetchall()
        items = [
            {
                "id": r[0],
                "received_at": r[1],
                "source_ip": r[2],
                "host": r[3],
                "facility": r[4],
                "severity": r[5],
                "program": r[6],
                "pid": r[7],
                "message": r[8],
                "pattern_id": r[9],
            }
            for r in rows
        ]
        return items, total
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
            "SELECT id, pattern_hash, pattern_text, sample_message, classification, ai_explanation, user_override, match_regex, title, host, program, hit_count, first_seen_at, last_seen_at FROM patterns WHERE pattern_hash = ?",
            (pattern_hash,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "pattern_hash": row[1],
            "pattern_text": row[2],
            "sample_message": row[3],
            "classification": row[4],
            "ai_explanation": row[5],
            "user_override": row[6],
            "match_regex": row[7],
            "title": row[8],
            "host": row[9],
            "program": row[10],
            "hit_count": row[11],
            "first_seen_at": row[12],
            "last_seen_at": row[13],
        }
    finally:
        disconnect_from_db(conn)


def insert_pattern(
    pattern_hash, pattern_text, sample_message, host=None, program=None, timestamp=None
):
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
                "id": r[0],
                "pattern_hash": r[1],
                "pattern_text": r[2],
                "sample_message": r[3],
                "host": r[4],
                "program": r[5],
                "hit_count": r[6],
                "first_seen_at": r[7],
                "last_seen_at": r[8],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


def update_pattern_classification(
    pattern_id, classification, ai_explanation=None, match_regex=None, title=None
):
    def _update():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE patterns SET classification = ?, ai_explanation = ?, match_regex = ?, title = ? WHERE id = ?",
                (classification, ai_explanation, match_regex, title, pattern_id),
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


def move_low_patterns_to_noise():
    """Set user_override to 'noise' for patterns whose effective classification is 'low'."""

    def _update():
        conn = connect_to_db()
        if not conn:
            return 0
        try:
            cursor = conn.cursor()
            cursor.execute("""UPDATE patterns
                   SET user_override = 'noise'
                   WHERE COALESCE(user_override, classification) = 'low'
                     AND (user_override IS NULL OR user_override != 'noise')""")
            conn.commit()
            return cursor.rowcount
        finally:
            disconnect_from_db(conn)

    return execute_with_retry(_update)


def update_pattern_regex(pattern_id, match_regex):
    def _update():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE patterns SET match_regex = ? WHERE id = ?",
                (match_regex, pattern_id),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_update)


def update_pattern_title(pattern_id, title):
    def _update():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE patterns SET title = ? WHERE id = ?",
                (title, pattern_id),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_update)


def update_pattern_ai_explanation(pattern_id, ai_explanation):
    def _update():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE patterns SET ai_explanation = ? WHERE id = ?",
                (ai_explanation, pattern_id),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_update)


def get_all_patterns(limit=None, offset=0, classification=None):
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

        if limit is not None:
            cursor.execute(
                f"""SELECT id, pattern_hash, pattern_text, sample_message, classification,
                           ai_explanation, user_override, match_regex, title, host, program, hit_count,
                           first_seen_at, last_seen_at
                    FROM patterns {where}
                    ORDER BY last_seen_at DESC LIMIT ? OFFSET ?""",
                params + [limit, offset],
            )
        else:
            cursor.execute(
                f"""SELECT id, pattern_hash, pattern_text, sample_message, classification,
                           ai_explanation, user_override, match_regex, title, host, program, hit_count,
                           first_seen_at, last_seen_at
                    FROM patterns {where}
                    ORDER BY last_seen_at DESC""",
                params,
            )
        rows = cursor.fetchall()
        items = [
            {
                "id": r[0],
                "pattern_hash": r[1],
                "pattern_text": r[2],
                "sample_message": r[3],
                "classification": r[4],
                "ai_explanation": r[5],
                "user_override": r[6],
                "match_regex": r[7],
                "title": r[8],
                "host": r[9],
                "program": r[10],
                "hit_count": r[11],
                "first_seen_at": r[12],
                "last_seen_at": r[13],
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
                      ai_explanation, user_override, match_regex, title, host, program, hit_count,
                      first_seen_at, last_seen_at
               FROM patterns WHERE id = ?""",
            (pattern_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "pattern_hash": row[1],
            "pattern_text": row[2],
            "sample_message": row[3],
            "classification": row[4],
            "ai_explanation": row[5],
            "user_override": row[6],
            "match_regex": row[7],
            "title": row[8],
            "host": row[9],
            "program": row[10],
            "hit_count": row[11],
            "first_seen_at": row[12],
            "last_seen_at": row[13],
            "effective_classification": row[6] if row[6] else row[4],
        }
    finally:
        disconnect_from_db(conn)


def get_patterns_with_regex():
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("""SELECT id, match_regex, classification, user_override
               FROM patterns
               WHERE match_regex IS NOT NULL AND classification != 'pending'""")
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "match_regex": r[1],
                "effective_classification": r[3] if r[3] else r[2],
            }
            for r in rows
        ]
    finally:
        disconnect_from_db(conn)


# --- Alert operations ---


def insert_alert(
    created_at, log_id, pattern_id, severity, host, source_ip, message, reason, action
):
    def _insert():
        conn = connect_to_db()
        if not conn:
            return None
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO alerts (created_at, log_id, pattern_id, severity, host, source_ip, message, reason, action)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    created_at,
                    log_id,
                    pattern_id,
                    severity,
                    host,
                    source_ip,
                    message,
                    reason,
                    action,
                ),
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


def get_alerts(
    limit=100,
    offset=0,
    severity=None,
    host=None,
    source_ip=None,
    pattern_id=None,
    search=None,
):
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
                "id": r[0],
                "created_at": r[1],
                "log_id": r[2],
                "pattern_id": r[3],
                "severity": r[4],
                "host": r[5],
                "source_ip": r[6],
                "message": r[7],
                "reason": r[8],
                "action": r[9],
                "discord_sent": bool(r[10]),
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
                "id": r[0],
                "host": r[1],
                "source_ip": r[2],
                "first_seen_at": r[3],
                "last_seen_at": r[4],
                "log_count": r[5],
                "alert_count": r[6],
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
            "SELECT COUNT(*) FROM logs WHERE datetime(received_at) >= datetime('now', 'localtime', '-1 hour')"
        )
        logs_last_hour = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM logs WHERE datetime(received_at) >= datetime('now', 'localtime', '-24 hours')"
        )
        logs_last_24h = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM logs")
        total_logs = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM alerts WHERE created_at >= datetime('now', 'localtime', '-1 hour')"
        )
        alerts_last_hour = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM alerts WHERE created_at >= datetime('now', 'localtime', '-24 hours')"
        )
        alerts_last_24h = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM alerts")
        total_alerts = cursor.fetchone()[0]

        cursor.execute(
            "SELECT source_ip, host, log_count FROM hosts ORDER BY log_count DESC LIMIT 10"
        )
        top_hosts = [
            {"source_ip": r[0], "host": r[1], "log_count": r[2]}
            for r in cursor.fetchall()
        ]

        cursor.execute("SELECT COUNT(*) FROM patterns")
        total_patterns = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM patterns WHERE classification = 'pending'")
        pending_patterns = cursor.fetchone()[0]

        cursor.execute(
            "SELECT classification, COUNT(*) as cnt FROM patterns GROUP BY classification ORDER BY cnt DESC"
        )
        pattern_breakdown = [
            {"classification": r[0], "count": r[1]} for r in cursor.fetchall()
        ]

        db_size = 0
        if os.path.exists(MITE_DB_PATH):
            db_size = os.path.getsize(MITE_DB_PATH)

        cursor.execute(
            "SELECT COUNT(*) FROM ai_api_calls WHERE called_at >= datetime('now', 'localtime', '-24 hours')"
        )
        ai_api_calls_24h = cursor.fetchone()[0]

        return {
            "logs_last_hour": logs_last_hour,
            "logs_last_24h": logs_last_24h,
            "total_logs": total_logs,
            "alerts_last_hour": alerts_last_hour,
            "alerts_last_24h": alerts_last_24h,
            "total_alerts": total_alerts,
            "top_hosts": top_hosts,
            "total_patterns": total_patterns,
            "pending_patterns": pending_patterns,
            "pattern_breakdown": pattern_breakdown,
            "database_size_bytes": db_size,
            "ai_api_calls_24h": ai_api_calls_24h,
        }
    finally:
        disconnect_from_db(conn)


# --- AI API call tracking ---


def record_ai_api_call():
    """Record an AI API call timestamp."""

    def _record():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO ai_api_calls (called_at) VALUES (datetime('now', 'localtime'))"
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_record)


def get_ai_api_call_count_24h():
    """Return the number of AI API calls in the last 24 hours."""
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM ai_api_calls WHERE called_at >= datetime('now', 'localtime', '-24 hours')"
        )
        return cursor.fetchone()[0]
    finally:
        disconnect_from_db(conn)


# --- Settings operations ---


def get_setting(key, default=None):
    """Return the value for a settings key, or default if not set."""
    conn = connect_to_db()
    if not conn:
        return default
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default
    finally:
        disconnect_from_db(conn)


def set_setting(key, value):
    """Insert or update a settings key/value pair."""

    def _upsert():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now', 'localtime'))
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                                  updated_at = excluded.updated_at""",
                (key, value),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_upsert)


def get_all_settings():
    """Return all settings as a list of {key, value, updated_at} dicts."""
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value, updated_at FROM settings ORDER BY key")
        return [
            {"key": r[0], "value": r[1], "updated_at": r[2]} for r in cursor.fetchall()
        ]
    finally:
        disconnect_from_db(conn)


# --- Retention operations ---


def delete_logs(log_ids):
    """Delete specific logs by ID."""
    if not log_ids:
        return

    def _delete():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in log_ids)
            cursor.execute(
                f"DELETE FROM logs WHERE id IN ({placeholders})",
                list(log_ids),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_delete)


def delete_old_logs(days):
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM logs WHERE datetime(received_at) < datetime('now', 'localtime', ?)",
            (f"-{days} days",),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_all_logs():
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM logs")
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_all_alerts():
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM alerts")
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_alert(alert_id):
    conn = connect_to_db()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_pattern(pattern_id):
    conn = connect_to_db()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pattern_stats WHERE pattern_id = ?", (pattern_id,))
        cursor.execute("DELETE FROM alerts WHERE pattern_id = ?", (pattern_id,))
        cursor.execute("DELETE FROM logs WHERE pattern_id = ?", (pattern_id,))
        cursor.execute("DELETE FROM patterns WHERE id = ?", (pattern_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_all_patterns():
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pattern_stats")
        cursor.execute("DELETE FROM alerts")
        cursor.execute("DELETE FROM logs")
        cursor.execute("DELETE FROM patterns")
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_old_patterns(days):
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cutoff = f"-{days} days"
        cursor.execute(
            "SELECT id FROM patterns WHERE datetime(last_seen_at) < datetime('now', 'localtime', ?)",
            (cutoff,),
        )
        pattern_ids = [row[0] for row in cursor.fetchall()]
        if not pattern_ids:
            return 0

        placeholders = ",".join("?" for _ in pattern_ids)
        cursor.execute(
            f"DELETE FROM pattern_stats WHERE pattern_id IN ({placeholders})",
            pattern_ids,
        )
        cursor.execute(
            f"DELETE FROM alerts WHERE pattern_id IN ({placeholders})",
            pattern_ids,
        )
        cursor.execute(
            f"DELETE FROM logs WHERE pattern_id IN ({placeholders})",
            pattern_ids,
        )
        cursor.execute(
            f"DELETE FROM patterns WHERE id IN ({placeholders})",
            pattern_ids,
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
            "DELETE FROM alerts WHERE created_at < datetime('now', 'localtime', ?)",
            (f"-{days} days",),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_old_pattern_stats(hours=100):
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM pattern_stats WHERE hour_bucket < datetime('now', 'localtime', ?)",
            (f"-{hours} hours",),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


def delete_old_ai_api_calls(days=2):
    """Delete AI API call records older than the given number of days."""
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM ai_api_calls WHERE called_at < datetime('now', 'localtime', ?)",
            (f"-{days} days",),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)


# --- Pattern stats operations ---


def increment_pattern_stat(pattern_id, timestamp):
    def _upsert():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            # Truncate timestamp to hour bucket
            cursor.execute(
                """INSERT INTO pattern_stats (pattern_id, hour_bucket, hit_count)
                   VALUES (?, strftime('%Y-%m-%d %H:00:00', ?), 1)
                   ON CONFLICT(pattern_id, hour_bucket)
                   DO UPDATE SET hit_count = hit_count + 1""",
                (pattern_id, timestamp),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_upsert)


def _fill_hour_gaps(stats_list, hours):
    """Fill in missing hour buckets with count=0."""
    if not hours:
        return stats_list
    from datetime import datetime, timedelta

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=hours - 1)
    existing = {s["hour"]: s["count"] for s in stats_list}
    filled = []
    current = start
    while current <= now:
        bucket = current.strftime("%Y-%m-%d %H:00:00")
        filled.append({"hour": bucket, "count": existing.get(bucket, 0)})
        current += timedelta(hours=1)
    return filled


def get_pattern_stats(pattern_id, hours=100):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT hour_bucket, hit_count FROM pattern_stats
               WHERE pattern_id = ? AND hour_bucket >= datetime('now', 'localtime', ?)
               ORDER BY hour_bucket ASC""",
            (pattern_id, f"-{hours} hours"),
        )
        raw = [{"hour": r[0], "count": r[1]} for r in cursor.fetchall()]
        return _fill_hour_gaps(raw, hours)
    finally:
        disconnect_from_db(conn)


def get_all_pattern_stats(hours=100):
    conn = connect_to_db()
    if not conn:
        return {}
    try:
        cursor = conn.cursor()

        # Get all pattern IDs
        cursor.execute("SELECT id FROM patterns")
        all_pattern_ids = [r[0] for r in cursor.fetchall()]

        cursor.execute(
            """SELECT pattern_id, hour_bucket, hit_count FROM pattern_stats
               WHERE hour_bucket >= datetime('now', 'localtime', ?)
               ORDER BY pattern_id, hour_bucket ASC""",
            (f"-{hours} hours",),
        )
        raw_stats = {}
        for r in cursor.fetchall():
            pid = r[0]
            if pid not in raw_stats:
                raw_stats[pid] = []
            raw_stats[pid].append({"hour": r[1], "count": r[2]})

        # Include all patterns, even those with no recent stats
        result = {}
        for pid in all_pattern_ids:
            result[pid] = _fill_hour_gaps(raw_stats.get(pid, []), hours)
        return result
    finally:
        disconnect_from_db(conn)


def get_hourly_log_counts(hours=24):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT strftime('%Y-%m-%d %H:00:00', datetime(received_at)) AS hour_bucket, COUNT(*) AS cnt
               FROM logs WHERE datetime(received_at) >= datetime('now', 'localtime', ?)
               GROUP BY hour_bucket ORDER BY hour_bucket ASC""",
            (f"-{hours} hours",),
        )
        raw = [{"hour": r[0], "count": r[1]} for r in cursor.fetchall()]
        return _fill_hour_gaps(raw, hours)
    finally:
        disconnect_from_db(conn)


def get_hourly_alert_counts(hours=24):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT strftime('%Y-%m-%d %H:00:00', created_at) AS hour_bucket, COUNT(*) AS cnt
               FROM alerts WHERE created_at >= datetime('now', 'localtime', ?)
               GROUP BY hour_bucket ORDER BY hour_bucket ASC""",
            (f"-{hours} hours",),
        )
        raw = [{"hour": r[0], "count": r[1]} for r in cursor.fetchall()]
        return _fill_hour_gaps(raw, hours)
    finally:
        disconnect_from_db(conn)


def increment_noise_stat(timestamp):
    def _upsert():
        conn = connect_to_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO noise_stats (hour_bucket, hit_count)
                   VALUES (strftime('%Y-%m-%d %H:00:00', ?), 1)
                   ON CONFLICT(hour_bucket)
                   DO UPDATE SET hit_count = hit_count + 1""",
                (timestamp,),
            )
            conn.commit()
        finally:
            disconnect_from_db(conn)

    execute_with_retry(_upsert)


def get_hourly_noise_counts(hours=24):
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT hour_bucket, hit_count FROM noise_stats
               WHERE hour_bucket >= datetime('now', 'localtime', ?)
               ORDER BY hour_bucket ASC""",
            (f"-{hours} hours",),
        )
        raw = [{"hour": r[0], "count": r[1]} for r in cursor.fetchall()]
        return _fill_hour_gaps(raw, hours)
    finally:
        disconnect_from_db(conn)


def delete_old_noise_stats(hours=100):
    conn = connect_to_db()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM noise_stats WHERE hour_bucket < datetime('now', 'localtime', ?)",
            (f"-{hours} hours",),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        disconnect_from_db(conn)
