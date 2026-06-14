CONST_CREATE_LOGS_SQL = """
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        received_at TEXT NOT NULL,
        source_ip TEXT,
        host TEXT,
        facility TEXT,
        severity TEXT,
        program TEXT,
        pid TEXT,
        message TEXT NOT NULL,
        raw_message TEXT NOT NULL,
        processed INTEGER DEFAULT 0,
        pattern_id INTEGER,
        FOREIGN KEY(pattern_id) REFERENCES patterns(id)
    );
    CREATE INDEX IF NOT EXISTS idx_logs_received_at ON logs(received_at);
    CREATE INDEX IF NOT EXISTS idx_logs_host ON logs(host);
    CREATE INDEX IF NOT EXISTS idx_logs_source_ip ON logs(source_ip);
    CREATE INDEX IF NOT EXISTS idx_logs_program ON logs(program);
    CREATE INDEX IF NOT EXISTS idx_logs_processed ON logs(processed);
    CREATE INDEX IF NOT EXISTS idx_logs_pattern_id ON logs(pattern_id);
"""

CONST_CREATE_ALERTS_SQL = """
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        log_id INTEGER,
        pattern_id INTEGER,
        severity TEXT NOT NULL,
        host TEXT,
        source_ip TEXT,
        message TEXT NOT NULL,
        reason TEXT,
        action TEXT,
        discord_sent INTEGER DEFAULT 0,
        FOREIGN KEY(log_id) REFERENCES logs(id),
        FOREIGN KEY(pattern_id) REFERENCES patterns(id)
    );
    CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
    CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
    CREATE INDEX IF NOT EXISTS idx_alerts_host ON alerts(host);
    CREATE INDEX IF NOT EXISTS idx_alerts_pattern_id ON alerts(pattern_id);
"""

CONST_CREATE_HOSTS_SQL = """
    CREATE TABLE IF NOT EXISTS hosts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        host TEXT,
        source_ip TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        log_count INTEGER DEFAULT 0,
        alert_count INTEGER DEFAULT 0,
        UNIQUE(source_ip, host)
    );
    CREATE INDEX IF NOT EXISTS idx_hosts_source_ip ON hosts(source_ip);
"""

CONST_CREATE_PATTERNS_SQL = """
    CREATE TABLE IF NOT EXISTS patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_hash TEXT NOT NULL UNIQUE,
        pattern_text TEXT NOT NULL,
        sample_message TEXT NOT NULL,
        classification TEXT DEFAULT 'pending',
        ai_explanation TEXT,
        user_override TEXT,
        match_regex TEXT,
        title TEXT,
        host TEXT,
        program TEXT,
        hit_count INTEGER DEFAULT 1,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_patterns_hash ON patterns(pattern_hash);
    CREATE INDEX IF NOT EXISTS idx_patterns_classification ON patterns(classification);
"""

CONST_CREATE_AI_API_CALLS_SQL = """
    CREATE TABLE IF NOT EXISTS ai_api_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        called_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_ai_api_calls_called_at ON ai_api_calls(called_at);
"""

CONST_CREATE_PATTERN_STATS_SQL = """
    CREATE TABLE IF NOT EXISTS pattern_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_id INTEGER NOT NULL,
        hour_bucket TEXT NOT NULL,
        hit_count INTEGER DEFAULT 1,
        UNIQUE(pattern_id, hour_bucket),
        FOREIGN KEY(pattern_id) REFERENCES patterns(id)
    );
    CREATE INDEX IF NOT EXISTS idx_pattern_stats_pattern_id ON pattern_stats(pattern_id);
    CREATE INDEX IF NOT EXISTS idx_pattern_stats_hour_bucket ON pattern_stats(hour_bucket);
"""
