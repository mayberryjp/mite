# 🔔 Mite — Intelligent Syslog Monitoring for Homelabs

**Zero-rules syslog alerting powered by AI. No manual rule writing. No noise. Just signal.**

Mite ingests syslog messages from your infrastructure, automatically learns which patterns matter, and alerts you when something important happens. Whether you're running a homelab, small business network, or hybrid environment, Mite sees the noise and helps you focus on what matters.

---

## 🎯 The Problem Mite Solves

Traditional syslog monitoring requires you to write rules. Lots of rules. And they're often wrong—either too sensitive (alert fatigue) or too loose (you miss real issues).

**Mite flips the script:**
- **No manual rules to write** — Mite automatically extracts patterns from your logs
- **AI-powered classification** — Each pattern is analyzed by an LLM to determine importance
- **User override capability** — Don't agree with the AI? Override with one click
- **Alert only on critical/high patterns** — Automatically filter noise

---

## ✨ Key Features

### 📊 Automatic Pattern Learning
- Extracts normalized patterns from raw syslog messages
- Replaces dynamic values (IPs, timestamps, numbers) with placeholders
- Tracks pattern frequency and first/last seen timestamps
- Handles RFC 3164 syslog format with full message preservation

### 🤖 AI-Powered Classification
- Sends pending patterns to OpenAI-compatible LLMs in batches
- Classifies each pattern as: **high**, **medium**, or **low** (override to **critical** or **noise** manually)
- Provides AI-generated explanations for each classification
- Efficient batch processing with configurable API rate limits

### ⚡ Real-Time Alerting
- Automatically creates alerts for critical/high patterns
- Optional Discord webhook integration for notifications
- User-overridable classifications (override AI when needed)
- Tracks alert counts and severity by host

### 🗄️ Lightweight Storage
- SQLite database with optimized indexing
- Configurable log and alert retention periods
- Database size monitoring via API
- WAL mode for concurrent access

### 🔌 REST API
- Comprehensive API for logs, alerts, patterns, and hosts
- Paginated queries with flexible filtering
- Pattern statistics and pattern-specific insights
- Dashboard stats endpoint for real-time monitoring

### 📦 Container-Ready
- Single Docker container with supervisord
- Multiple independent workers (UDP listener, TCP listener, processor, AI worker, retention worker)
- Volume-based persistent storage
- Production-ready Dockerfile and docker-compose

---

## 🚀 Quick Start

### Docker Compose

Mite consists of a backend API and frontend web UI, both running in Docker.

**Backend (docker-compose.yml):**

```yaml
version: "3"
services:
  mite:
    image: mayberry4477/mite:latest
    container_name: mite
    restart: unless-stopped
    ports:
      - 4060:4060
      - "1514:1514/udp"
      - "1515:1515/tcp"
      - "8030:8030/tcp"
    volumes:
      - /docker/mite/data:/app/data
      - /docker/mite/logs:/app/logs
    environment:
      - MITE_API_HOST=0.0.0.0
      - MITE_API_PORT=4060
      - MITE_SYSLOG_UDP_HOST=0.0.0.0
      - MITE_SYSLOG_UDP_PORT=1514
      - MITE_SYSLOG_TCP_HOST=0.0.0.0
      - MITE_SYSLOG_TCP_PORT=1515
      - MITE_MCP_HOST=0.0.0.0
      - MITE_MCP_PORT=8030
      - AI_API_BASE_URL=
      - AI_API_KEY=
      - AI_MODEL=
      - TZ=Asia/Tokyo
```

**Frontend (docker-compose.yml):**

```yaml
version: "3"
services:
  mite-web:
    image: mayberry4477/mite-web:latest
    container_name: mite-web
    restart: unless-stopped
    ports:
      - "4050:4050"
    environment:
      - MITE_API_BASE_URL=http://192.168.4.4:4060/api
      - TZ=Asia/Tokyo
```

**Ports:**
- **Web UI:** 4050 (Frontend dashboard)
- **API:** 4060 (REST endpoints)
- **Syslog UDP:** 1514
- **Syslog TCP:** 1515
- **MCP:** 8030 (JSON-RPC endpoint at `/mcp`)

> **Docker Hub**: [mayberry4477/mite](https://hub.docker.com/r/mayberry4477/mite) · [mayberry4477/mite-web](https://hub.docker.com/r/mayberry4477/mite-web)

---

## ⚙️ Configuration

All configuration is via **environment variables**. Set them in your shell or `docker-compose.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MITE_API_HOST` | `0.0.0.0` | API server bind address |
| `MITE_API_PORT` | `4060` | API server port |
| `MITE_SYSLOG_UDP_HOST` | `0.0.0.0` | UDP listener bind address |
| `MITE_SYSLOG_UDP_PORT` | `1514` | UDP listener port |
| `MITE_SYSLOG_TCP_HOST` | `0.0.0.0` | TCP listener bind address |
| `MITE_SYSLOG_TCP_PORT` | `1515` | TCP listener port |
| `MITE_MCP_HOST` | `0.0.0.0` | MCP server bind address |
| `MITE_MCP_PORT` | `8030` | MCP server port |
| `AI_API_BASE_URL` | `` | OpenAI-compatible API endpoint (required for AI) |
| `AI_API_KEY` | `` | API key for LLM (required for AI) |
| `AI_MODEL` | `` | Model name (e.g., `gpt-4-turbo-preview`) |
| `TZ` | `UTC` | Timezone for timestamps |

Storage paths are fixed inside the container and are not configurable: the main
database is at `/app/data/mite.db`, the logs database at `/app/data/logs.db`, and
application logs are written under `/app/logs`. Mount a volume at `/app/data` to
persist data.

### AI Configuration

To enable AI classification, set these environment variables:

```bash
AI_API_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-...
AI_MODEL=gpt-4-turbo-preview
```

Mite also supports any OpenAI-compatible API (local LLMs, Ollama, Azure OpenAI, etc.).

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Container                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  supervisord (Process Manager)                       │   │
│  ├──────────────────────────────────────────────────────┤   │
│  │                                                      │   │
│  │  • UDP Listener (port 1514) ──┐                    │   │
│  │  • TCP Listener (port 1515) ──┤                    │   │
│  │                               ├─→ SQLite (logs)    │   │
│  │  • Processor                  │   ├─→ patterns     │   │
│  │  • AI Worker                  │   ├─→ alerts       │   │
│  │  • Retention Worker      ─────┴──→ └─→ hosts       │   │
│  │                                                      │   │
│  │  • API Server (port 4060)                           │   │
│  │    └─→ REST endpoints (logs, alerts, patterns...)   │   │
│  │  • MCP Server (port 8030)                           │   │
│  │    └─→ Streamable HTTP JSON-RPC tools on /mcp       │   │
│  │                                                      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
Syslog Sources → UDP/TCP Listeners → SQLite logs table (processed=0)
                                           ↓
                        Processor extracts normalized pattern
                                           ↓
                      Look up pattern in patterns table
                           ↓                    ↓
                    Known Pattern          New Pattern
                   (increment hit)        (mark pending)
                           ↓                    ↓
              Check effective classification   ↓
              (user_override > AI)    AI Worker classifies
                           ↓
              critical/high → Create Alert
                           ↓
           Mark log processed + link to pattern

```

### Components

- **UDP/TCP Listeners**: Accept RFC 3164 syslog messages, batch insert into database
- **Processor**: Polls unprocessed logs every 10s, extracts patterns, creates alerts for critical/high patterns
- **AI Worker**: Picks up pending patterns, sends to LLM in batches, stores classifications
- **Retention Worker**: Deletes old logs and alerts based on configurable retention periods
- **API Server**: Bottle + Waitress, serves REST endpoints for UI and external integrations
- **MCP Server**: Bottle + Waitress JSON-RPC endpoint at `/mcp` for MCP tool access

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/stats` | Dashboard statistics (total logs, alerts, patterns, DB size) |
| `GET` | `/api/logs` | Paginated logs with filtering by host, IP, program, severity |
| `GET` | `/api/logs/recent` | Poll for newly received logs |
| `GET` | `/api/alerts` | Paginated alerts with filtering |
| `GET` | `/api/patterns` | All patterns with classifications |
| `GET` | `/api/patterns/<id>` | Single pattern details with sample message |
| `PUT` | `/api/patterns/<id>` | Override pattern classification |
| `GET` | `/api/ai/pending` | Patterns awaiting AI classification |
| `POST` | `/api/ai/classify` | Manually trigger AI classification |
| `POST` | `/api/discord/test` | Test Discord webhook configuration |

---

## 🔄 How It Works

### Step 1: Ingest Syslog
Your infrastructure sends syslog messages to Mite's UDP (1514) or TCP (1515) ports. Mite parses RFC 3164 format and stores raw messages in SQLite.

### Step 2: Extract Patterns
The processor runs every 10 seconds and:
1. Picks up unprocessed logs
2. Normalizes each message (replaces dynamic values with placeholders)
3. Computes a hash of the normalized pattern
4. Looks up the pattern in the database:
   - **If known**: Increments hit count
   - **If new**: Inserts as "pending" for AI classification

### Step 3: AI Classification
The AI worker:
1. Batches up pending patterns (default: 20 per batch)
2. Sends them to your LLM with context about infrastructure
3. Receives classifications: high, medium, or low (critical and noise are set only via user override)
4. Stores classification and AI-generated explanation

### Step 4: Alert & Track
For each log linked to a critical/high pattern:
1. Create an alert in the alerts table
2. Optionally send Discord notification
3. Respect user overrides (if user marked pattern as "noise", skip alert)

### Step 5: Retention
Old logs and alerts are automatically deleted based on configurable retention periods (default: 14 days logs, 30 days alerts).

---

## 📊 Pattern Examples

### Before: Raw Syslog
```
Jun 15 10:22:33 proxmox pvestatd[1234]: status update: 192.168.1.5 disk usage 78%
Jun 15 10:22:45 proxmox pvestatd[1234]: status update: 192.168.1.8 disk usage 75%
Jun 15 10:23:01 proxmox pvestatd[1234]: status update: 192.168.1.10 disk usage 82%
```

### After: Normalized Pattern
```
Pattern: pvestatd.*status update.*disk usage <N>%
Hash: abc123def456
Sample: pvestatd[1234]: status update: 192.168.1.5 disk usage 78%
Hit Count: 3
Classification: low (normal system monitoring)
```

---

## 🛠️ Development

### Tech Stack
- **Language**: Python 3.12
- **Web Framework**: Bottle (lightweight, minimal dependencies)
- **WSGI Server**: Waitress (production-grade)
- **Database**: SQLite (zero external dependencies)
- **Process Manager**: supervisord
- **Container**: Docker

### Project Structure
```
mite/
├── src/
│   ├── api/
│   │   ├── server.py                 # Main Bottle app, route setup
│   │   ├── routes_logs.py           # Log-related endpoints
│   │   ├── routes_alerts.py         # Alert-related endpoints
│   │   ├── routes_rules.py          # Pattern management
│   │   ├── routes_discovery.py      # AI classification triggers
│   │   └── routes_settings.py       # User-editable settings
│   ├── core/
│   │   ├── config.py                 # Environment variable parsing
│   │   ├── db.py                     # Database operations
│   │   ├── models.py                 # SQL schemas, AI prompt templates
│   │   ├── pattern_extractor.py      # Pattern normalization and hashing
│   │   ├── syslog_parser.py          # RFC 3164 parser
│   │   ├── ai_discovery.py           # LLM integration
│   │   ├── discord.py                # Discord webhook
│   │   └── retention.py              # Retention policy executor
│   ├── workers/
│   │   ├── udp_listener.py          # UDP socket listener
│   │   ├── tcp_listener.py          # TCP socket listener
│   │   ├── processor.py             # Pattern extraction and alerting
│   │   ├── ai_worker.py             # Batch AI classification
│   │   └── retention_worker.py      # Cleanup old data
│   ├── utils/
│   │   └── locallogging.py          # Logging helpers
│   └── main.py                       # Initialization script
├── Dockerfile                        # Container definition
├── docker-compose.yml               # Multi-container orchestration
├── supervisord.conf                 # Worker process config
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

### Running Tests
Currently, Mite doesn't have automated tests configured. For development, use:

```bash
# Check code style
python -m flake8 src/
python -m black --check src/
python -m isort --check-only src/

# Format code
python -m black src/
python -m isort src/
```

---

## 🔐 Security Considerations

- **No authentication by default** — Mite is designed for private networks (homelabs, internal infrastructure)
- **Bind to localhost** in untrusted networks: `MITE_API_HOST=127.0.0.1`
- **Validate API keys** if exposing via reverse proxy
- **Rate limiting** — Implement at your load balancer/reverse proxy
- **Discord webhook secrets** — Keep `DISCORD_WEBHOOK_URL` secure, don't commit to git

---

## 📈 Performance Notes

- **Typical ingest rate**: Thousands of logs/second (memory and network dependent)
- **Pattern extraction**: <1ms per log
- **AI batching**: Configurable batch size (default: 20 patterns per LLM call)
- **AI batch frequency**: Configurable interval (default: 1 hour)
- **Database**: WAL mode for concurrent read/write

---

## 🤝 Contributing

This is an open-source project. Contributions are welcome!

To contribute:
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -am 'Add feature'`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a pull request

---

## 📝 License

[Add your license here — MIT, Apache 2.0, etc.]

---

## 💬 Support & Community

- **Issues**: Report bugs and feature requests on GitHub
- **Discussions**: Ask questions in GitHub Discussions
- **Discord**: [Add Discord server link if applicable]

---

## 🎓 Learn More

- [Pattern Extraction Guide](docs/pattern-extraction.md)
- [AI Configuration Guide](docs/ai-configuration.md)
- [API Reference](docs/api-reference.md)
- [Architecture Deep Dive](docs/architecture.md)

---

**Made with ❤️ for infrastructure engineers who want smarter alerting.**
