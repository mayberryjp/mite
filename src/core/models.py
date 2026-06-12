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
        ai_candidate INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_logs_received_at ON logs(received_at);
    CREATE INDEX IF NOT EXISTS idx_logs_host ON logs(host);
    CREATE INDEX IF NOT EXISTS idx_logs_source_ip ON logs(source_ip);
    CREATE INDEX IF NOT EXISTS idx_logs_program ON logs(program);
    CREATE INDEX IF NOT EXISTS idx_logs_processed ON logs(processed);
    CREATE INDEX IF NOT EXISTS idx_logs_ai_candidate ON logs(ai_candidate);
"""

CONST_CREATE_ALERTS_SQL = """
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        log_id INTEGER,
        rule_name TEXT NOT NULL,
        severity TEXT NOT NULL,
        host TEXT,
        source_ip TEXT,
        message TEXT NOT NULL,
        reason TEXT,
        action TEXT,
        discord_sent INTEGER DEFAULT 0,
        FOREIGN KEY(log_id) REFERENCES logs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
    CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
    CREATE INDEX IF NOT EXISTS idx_alerts_host ON alerts(host);
    CREATE INDEX IF NOT EXISTS idx_alerts_rule_name ON alerts(rule_name);
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

CONST_CREATE_RULE_COOLDOWNS_SQL = """
    CREATE TABLE IF NOT EXISTS rule_cooldowns (
        rule_name TEXT NOT NULL,
        cooldown_key TEXT NOT NULL,
        last_sent_at TEXT NOT NULL,
        PRIMARY KEY(rule_name, cooldown_key)
    );
"""

CONST_CREATE_AI_ANALYSES_SQL = """
    CREATE TABLE IF NOT EXISTS ai_analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        source_ip TEXT,
        host TEXT,
        sample_count INTEGER NOT NULL,
        markdown_path TEXT NOT NULL,
        status TEXT NOT NULL,
        summary TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_ai_analyses_status ON ai_analyses(status);
"""
