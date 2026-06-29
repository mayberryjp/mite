import json
import os
import sqlite3
import tempfile

base = tempfile.mkdtemp()


def fresh_dir(name):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    return d


# ---- Scenario 1: fresh install WITH patterns_import.json ----
d1 = fresh_dir("with_file")
db1 = os.path.join(d1, "Mite.sqlite")
import_payload = {
    "export_version": 1,
    "exported_at": "2026-06-28T21:18:54",
    "mite_version": "test",
    "count": 2,
    "patterns": [
        {
            "id": 99,
            "pattern_hash": "h1",
            "pattern_text": "Failed login for <USER> from <IP>",
            "sample_message": "Failed login for admin from 10.0.0.5",
            "classification": "high",
            "user_override": "critical",
            "hit_count": 0,
            "host": "fw01",
            "program": "sshd",
            "first_seen_at": "2026-06-28 10:00:00",
            "last_seen_at": "2026-06-28 12:00:00",
            "filter_at_listener": True,
            "effective_classification": "critical",
        },
        # duplicate hash should be ignored
        {
            "pattern_hash": "h1",
            "pattern_text": "dup",
            "sample_message": "dup",
            "first_seen_at": "2026-06-28 10:00:00",
            "last_seen_at": "2026-06-28 12:00:00",
        },
        # missing required field -> skipped
        {"pattern_hash": "h2"},
    ],
}
with open(os.path.join(d1, "patterns_import.json"), "w", encoding="utf-8") as f:
    json.dump(import_payload, f)

os.environ["MITE_DB_PATH"] = db1
import importlib
import src.core.config as config
import src.core.db as db

importlib.reload(config)
importlib.reload(db)

db.init_database()
conn = sqlite3.connect(db1)
rows = conn.execute(
    "SELECT pattern_hash, classification, user_override, hit_count, filter_at_listener FROM patterns ORDER BY pattern_hash"
).fetchall()
conn.close()
print("Scenario 1 (with file) rows:", rows)
s1 = rows == [("h1", "high", "critical", 0, 1)]
print("Scenario 1 PASS:", s1)

# ---- Scenario 1b: restart (DB already exists) should NOT reseed/duplicate ----
db.init_database()
conn = sqlite3.connect(db1)
count_after_restart = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
conn.close()
print("Scenario 1b count after restart:", count_after_restart)
s1b = count_after_restart == 1
print("Scenario 1b PASS:", s1b)

# ---- Scenario 2: fresh install with NO import file ----
d2 = fresh_dir("no_file")
db2 = os.path.join(d2, "Mite.sqlite")
os.environ["MITE_DB_PATH"] = db2
importlib.reload(config)
importlib.reload(db)
db.init_database()  # should not crash
conn = sqlite3.connect(db2)
count2 = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
conn.close()
print("Scenario 2 (no file) pattern count:", count2)
s2 = count2 == 0
print("Scenario 2 PASS:", s2)

# ---- Scenario 3: fresh install with malformed import file ----
d3 = fresh_dir("bad_file")
db3 = os.path.join(d3, "Mite.sqlite")
with open(os.path.join(d3, "patterns_import.json"), "w", encoding="utf-8") as f:
    f.write("{ this is not valid json ]")
os.environ["MITE_DB_PATH"] = db3
importlib.reload(config)
importlib.reload(db)
db.init_database()  # should not crash
conn = sqlite3.connect(db3)
count3 = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
conn.close()
print("Scenario 3 (bad file) pattern count:", count3)
s3 = count3 == 0
print("Scenario 3 PASS:", s3)

print("ALL PASS:", all([s1, s1b, s2, s3]))
