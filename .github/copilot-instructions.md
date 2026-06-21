# Copilot Instructions for Mite

## What is Mite

Mite is a lightweight, hands-off syslog monitoring and alerting tool for homelabs. It receives syslog messages over UDP (port 1514) and TCP (port 1515), stores them in SQLite, automatically extracts log patterns, and uses AI to classify each pattern's importance. Users don't write rules — the system learns which log patterns matter and which are noise. Users can view and override AI classifications via the API. Only critical/high patterns generate alerts and optional Discord notifications.

## Architecture

Mite runs as multiple independent processes managed by supervisord inside a single Docker container:

- **`src.api.server`** — Bottle + Waitress REST API. Routes are split into `routes_logs`, `routes_alerts`, `routes_rules` (pattern management), and `routes_discovery` (AI classification).
- **`src.workers.udp_listener`** / **`src.workers.tcp_listener`** — Socket listeners that parse incoming syslog (RFC 3164) and insert raw logs into the `logs` table.
- **`src.workers.processor`** — Polls `logs` for unprocessed rows every 10 seconds, extracts a normalized pattern from each message, looks up or creates the pattern in the `patterns` table, and creates alerts for critical/high patterns.
- **`src.workers.ai_worker`** — Periodically picks up pending (unclassified) patterns and sends them to an OpenAI-compatible API in batches for classification.
- **`src.workers.retention_worker`** — Deletes logs and alerts older than configured retention periods.

### Data flow

```
Syslog sources → UDP/TCP listeners → SQLite (logs table, processed=0)
                                          ↓
                               Processor picks up unprocessed logs
                                          ↓
                               Extract pattern → normalize message
                                          ↓
                          Look up pattern hash in patterns table
                            ↓                              ↓
                      Known pattern                  New pattern
                      (increment hit)              (insert as pending)
                            ↓                              ↓
                   Check effective classification    AI worker classifies
                   (user_override > ai classification)   in batches
                            ↓
                   critical/high → insert alert + Discord
                            ↓
                   Mark log processed with pattern_id
```

### Pattern system

Instead of manual rules, Mite automatically identifies log patterns:

1. **Pattern extraction** (`src/core/pattern_extractor.py`): Normalizes log messages by replacing dynamic values (IPs → `<IP>`, numbers → `<N>`, timestamps → `<TS>`, UUIDs → `<UUID>`, etc.) with placeholders, producing a stable pattern signature.
2. **Pattern storage**: Patterns are stored in the `patterns` table with a unique hash. Each pattern tracks hit count, first/last seen, AI classification, and optional user override.
3. **AI classification**: The AI worker sends unclassified patterns (with sample messages) to an LLM in batches and receives classifications: `critical`, `high`, `medium`, `low`, or `noise`.
4. **User override**: Users can override any AI classification via `PUT /api/patterns/<id>` with `{"classification": "noise"}` (or any valid level).
5. **Effective classification**: `user_override` takes precedence over `classification`. Only `critical` and `high` patterns generate alerts.

## Key Conventions

### Database access pattern
Every DB operation opens a new `sqlite3.connect()`, does its work, and closes immediately — there is no connection pool or long-lived connection. Use `connect_to_db()` / `disconnect_from_db()` from `src.core.db`. Write operations that may hit lock contention should use `execute_with_retry()`.

### Configuration
All config is via environment variables with defaults in `src/core/config.py`. Variables are read at module import time as module-level constants (e.g., `MITE_DB_PATH`, `AI_DISCOVERY_ENABLED`). All state is stored in SQLite — no filesystem-based configuration.

### Logging
Use the wrappers in `src/utils/locallogging.py` (`log_info`, `log_error`, `log_warn`) rather than calling `logger.info()` directly. Each module creates its own `logger = logging.getLogger(__name__)`.

### API pattern
All API routes return JSON via `json.dumps()`. Route handlers follow a try/except pattern logging errors with `log_error`. CORS is handled globally via an `after_request` hook. Routes are organized by resource in `src/api/routes_*.py` files, each exporting a `setup_*_routes(app)` function.

### Worker startup
Each worker script sleeps 5-10 seconds on startup to wait for database initialization, then calls `init_database()` before entering its main loop.

## API Endpoints

- `GET /api/logs` — Paginated log retrieval with filters
- `GET /api/logs/recent` — Poll for new logs
- `GET /api/alerts` — Paginated alert retrieval with filters
- `GET /api/patterns` — List all patterns (filterable by classification)
- `GET /api/patterns/<id>` — Get single pattern details
- `PUT /api/patterns/<id>` — Override pattern classification
- `GET /api/ai/pending` — List patterns awaiting AI classification
- `POST /api/ai/classify` — Manually trigger AI classification
- `GET /api/stats` — Dashboard statistics
- `GET /api/health` — Health check

## Running

```bash
# Run with Docker Compose (pulls from Docker Hub)
docker compose up -d

# Run a single component locally (requires env vars or defaults to /app/* paths)
python -m src.api.server
python -m src.workers.processor
```

## Dependencies

Python 3.12, Bottle (web framework), Waitress (WSGI server), Requests. No test framework is currently configured.
