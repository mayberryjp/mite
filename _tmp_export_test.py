import json
import os
import sqlite3
import tempfile

tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "Mite.sqlite")
os.environ["MITE_DB_PATH"] = db_path  # must be set before importing config/db

from src.core.models import CONST_CREATE_PATTERNS_SQL  # noqa: E402

conn = sqlite3.connect(db_path)
conn.executescript(CONST_CREATE_PATTERNS_SQL)
conn.execute(
    """INSERT INTO patterns
       (pattern_hash, pattern_text, sample_message, classification, host, program,
        hit_count, first_seen_at, last_seen_at)
       VALUES ('hash1', 'Failed login for <USER> from <IP>',
               'Failed login for admin from 10.0.0.5', 'high', 'fw01', 'sshd',
               4242, '2026-06-28 10:00:00', '2026-06-28 12:00:00')"""
)
conn.commit()
conn.close()

from src.api.routes_rules import export_patterns_to_file  # noqa: E402

result = export_patterns_to_file()
with open(result["path"], "r", encoding="utf-8") as f:
    data = json.load(f)

exported_hit = data["patterns"][0]["hit_count"]
print("Exported hit_count (want 0):", exported_hit)

# Confirm the DB was NOT modified
conn = sqlite3.connect(db_path)
db_hit = conn.execute("SELECT hit_count FROM patterns WHERE pattern_hash='hash1'").fetchone()[0]
conn.close()
print("DB hit_count (want 4242):", db_hit)
print("PASS:", exported_hit == 0 and db_hit == 4242)
