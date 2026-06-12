# Copilot Instructions for Mite

## What is Mite

Mite is a lightweight syslog monitoring and alerting tool for homelabs. It receives syslog messages over UDP (port 1514) and TCP (port 1515), stores them in SQLite, evaluates them against YAML-defined rules, and fires alerts with optional Discord notifications. It also has an optional AI discovery feature that analyzes log samples to auto-generate new rules.

## Architecture

Mite runs as multiple independent processes managed by supervisord inside a single Docker container:

- **`src.api.server`** — Bottle + Waitress REST API on port 8080. Routes are split into `routes_logs`, `routes_alerts`, `routes_hosts`, `routes_rules`, and `routes_discovery`.
- **`src.workers.udp_listener`** / **`src.workers.tcp_listener`** — Socket listeners that parse incoming syslog (RFC 3164) and insert raw logs into the `logs` table.
- **`src.workers.processor`** — Polls `logs` for unprocessed rows every 10 seconds, evaluates each against all loaded rules, creates alerts, and sends Discord notifications (with cooldown).
- **`src.workers.ai_worker`** — Periodically calls an OpenAI-compatible API to analyze log samples from unanalyzed hosts and generates markdown files containing new rules.
- **`src.workers.retention_worker`** — Deletes logs and alerts older than configured retention periods.

### Data flow

```
Syslog sources → UDP/TCP listeners → SQLite (logs table, processed=0)
                                          ↓
                               Processor worker picks up unprocessed logs
                                          ↓
                               Rule engine evaluates each log against all rules
                                          ↓
                            Matching → insert alert + optional Discord notification
                                          ↓
                               Mark log as processed=1
```

### Rule loading

Rules are loaded from three sources (merged in this order):
1. `config/rules.yml` — user-defined YAML rules
2. `rules/*.yml` — additional YAML rule files
3. `analysis/*.md` — AI-generated markdown files containing embedded YAML rule blocks

Rules are re-loaded from disk on every processor cycle, so changes take effect without restart.

## Key Conventions

### Database access pattern
Every DB operation opens a new `sqlite3.connect()`, does its work, and closes immediately — there is no connection pool or long-lived connection. Use `connect_to_db()` / `disconnect_from_db()` from `src.core.db`. Write operations that may hit lock contention should use `execute_with_retry()`.

### Configuration
All config is via environment variables with defaults in `src/core/config.py`. Variables are read at module import time as module-level constants (e.g., `MITE_DB_PATH`, `AI_DISCOVERY_ENABLED`).

### Logging
Use the wrappers in `src/utils/locallogging.py` (`log_info`, `log_error`, `log_warn`, `log_debug`) rather than calling `logger.info()` directly. Each module creates its own `logger = logging.getLogger(__name__)`.

### Rule schema
Rules are YAML dicts with this structure:
```yaml
- name: "Rule name"
  enabled: true
  severity: "high"          # high | medium | low | critical
  description: "..."
  match:
    contains_any: []         # message substring match (OR)
    contains_all: []         # message substring match (AND)
    regex_any: []            # regex match on message (OR)
    regex_all: []            # regex match on message (AND)
    host_any: []
    source_ip_any: []
    program_any: []
    severity_any: []
    facility_any: []
  cooldown_seconds: 300
  cooldown_key: "rule_host"  # rule_only | rule_host | rule_host_message | rule_source_ip | rule_source_ip_message
  discord: true
  action: "Description of recommended action"
```

### API pattern
All API routes return JSON via `json.dumps()`. Route handlers follow a try/except pattern logging errors with `log_error`. CORS is handled globally via an `after_request` hook. Routes are organized by resource in `src/api/routes_*.py` files, each exporting a `setup_*_routes(app)` function.

### Worker startup
Each worker script sleeps 5-10 seconds on startup to wait for database initialization, then calls `init_database()` before entering its main loop.

## Running

```bash
# Build and run with Docker Compose
docker compose up -d --build

# Run a single component locally (requires env vars or defaults to /app/* paths)
python -m src.api.server
python -m src.workers.processor
```

## Dependencies

Python 3.12, Bottle (web framework), Waitress (WSGI server), PyYAML, Requests. No test framework is currently configured.
