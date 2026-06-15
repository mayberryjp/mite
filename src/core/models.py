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
        called_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
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

CONST_CREATE_NOISE_STATS_SQL = """
    CREATE TABLE IF NOT EXISTS noise_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hour_bucket TEXT NOT NULL UNIQUE,
        hit_count INTEGER DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_noise_stats_hour_bucket ON noise_stats(hour_bucket);
"""

CONST_CREATE_SETTINGS_SQL = """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
"""

DEFAULT_AI_PROMPT_TEMPLATE = """I am an infrastructure engineer whose job is to review and classify logs for a network containing servers, firewalls, routers, switches, wireless access points, VPNs, Docker hosts, databases, monitoring systems, and other infrastructure devices.

Please help me understand the following logs and decide whether they are important for alerting.

Each log pattern below includes:

- a pattern ID
- a sample of the original log message

For each log pattern, you must:

1. Explain what this log pattern means in plain language: what system, service, daemon, or device likely produces it, what event it represents, whether it indicates normal behavior, degraded service, system failure, security risk, or misconfiguration, and why it matters or does not matter operationally.
2. Classify its importance for alerting using exactly one of: "high", "medium", or "low".

Classification guidance:

Use "high" for logs that need immediate attention or could indicate a serious incident.

High severity includes, but is not limited to:

- system down, critical service unavailable, repeated service crashes, kernel panic, filesystem corruption, disk failure, RAID failure, database corruption
- out-of-memory killer terminating important services, disk full on important volumes, backup failure for critical systems
- VPN tunnel down, DNS, DHCP, firewall, router, storage, authentication, monitoring, or syslog service failure
- network interface down on critical infrastructure
- repeated authentication failures suggesting brute force, successful suspicious login, privilege escalation
- unexpected root, sudo, or admin activity, new admin user created unexpectedly
- malware, exploit, IDS/IPS, or rootkit alerts
- access to secrets, keys, credentials, sensitive files, or system configuration
- firewall or security policy changes
- events that suggest data loss, compromise, outage, or immediate business impact

Security violations and system failure issues should generally be classified higher. If a log suggests a real compromise, active attack, data loss, critical infrastructure failure, or service outage, classify it as "high" even if the syslog level says notice, minor, warning, or info.

Use "medium" for logs that are worth monitoring or investigating but are not clearly urgent.

Medium severity includes, but is not limited to:

- unusual service restarts, service degradation, temporary DNS timeout, temporary API timeout, packet loss, high latency
- wireless client problems, high WiFi channel utilization, repeated client disconnects
- Docker container health check failures that recover
- configuration warnings, certificate warnings before expiration
- AppArmor or SELinux denials that may affect a non-critical service
- missing optional configuration files, deprecated settings, rate limiting
- resource pressure that is not yet critical (high CPU, memory, disk, temperature, or queue depth without immediate failure)
- blocked traffic that is unusual, repeated, internal, or targeting sensitive services
- failed SSH or login attempts that are suspicious but limited
- recurring low-severity events that may indicate misconfiguration, scanning, noise, or device malfunction

Use "low" for logs that are informational, routine, expected, benign, or not useful for alerting.

Low severity includes, but is not limited to:

- scheduled tasks completed, cron session open/close, normal service start or stop
- DHCP lease renewal, NTP sync, routine DNS activity, health check success
- Docker container started successfully
- expected firewall blocks from internet background noise, random inbound scans blocked by firewall
- TCP resets with no evidence of failure
- expected multicast, broadcast, SSDP, UPnP, mDNS, IGMP, or IPv6 neighbor discovery noise
- normal wireless association/disassociation messages unless repeated or widespread
- benign IoT or consumer device discovery traffic
- single transient timeout with no service impact
- informational kernel, daemon, firewall, AP, or container messages
- AppArmor or SELinux audit messages that do not block important functionality

Do not classify severity based only on the syslog level.

Escalate severity when:

- the affected system is critical infrastructure
- the event involves authentication, authorization, admin access, firewall rules, secrets, credentials, keys, or sensitive files
- the event suggests service outage, data loss, corruption, disk failure, or system instability
- the same event appears repeatedly
- the event affects multiple clients, services, containers, or network segments
- a normally harmless event occurs at very high volume
- a blocked or denied event becomes successful or allowed
- the log involves internal hosts scanning, attacking, or behaving unexpectedly

De-escalate severity when:

- the event was blocked successfully and looks like routine background noise
- the source and destination appear to be normal infrastructure or known benign devices
- the event is isolated and self-recovered
- the denied action involves a non-critical optional file
- the message is informational or expected during normal operation
- there is no evidence of user impact, service failure, compromise, or data risk

3. Create a Python-compatible regular expression that robustly matches this type of log message.

The regex must:

- work with Python re.search() and not require matching the full line
- match the static/structural parts of the log using keywords, field names, program names, daemon names, protocol names, and stable message text
- use \\S+, .+?, \\d+, [0-9a-fA-F]+, or similar patterns for dynamic values
- be specific enough to match this type of log, not overly broad
- be general enough to match future logs of the same pattern
- avoid embedding one-time values unless they define the event type

Dynamic values to generalize: timestamps, dates, hostnames, source/destination IPs, MAC addresses, ports, PIDs, sequence numbers, counters, byte counts, hex values, UUIDs.

Values that may be kept when they define the event type: daemon/program names, destination ports that define the protocol, protocol names, stable phrases.

Output requirements:

Respond ONLY with a JSON array. Do not include Markdown, code fences, comments, or explanatory text.

Each element must have exactly these fields:

- "id": the pattern ID as an integer from the input
- "classification": exactly one of "high", "medium", "low"
- "description": 2-4 sentences explaining what this log pattern means, what produces it, and why it matters or does not matter. Write as if explaining to a fellow engineer.
- "match_regex": a Python regex string that matches this type of log message
- "title": a short human-readable title (max 40 chars) describing the type of log event. Must not contain timestamps, dates, IPs, hostnames, MACs, or one-off values.

Patterns to analyze:

{patterns}"""

Your job is to analyze one log entry or a small batch of related log entries and classify the event into one of these severity buckets:

- HIGH
- MEDIUM
- LOW

You must focus on operational impact, security risk, likelihood of real failure, and whether immediate human attention is required.

You must return only the required output format. Do not include extra prose before or after the result.

Severity Guidelines

HIGH severity means the log may require immediate attention because it suggests active security violations, service outage, system failure, data loss risk, or critical infrastructure impact.

Classify as HIGH when the log suggests:

- Kernel panic, system crash, reboot loop, filesystem corruption, disk failure, RAID failure, database corruption, or storage write failure
- Critical service unavailable, repeatedly crashing, or unable to start
- Firewall, router, DNS, DHCP, VPN, storage, database, syslog, monitoring, or authentication failure
- Network interface down on critical infrastructure
- Docker daemon failure or important container repeatedly restarting
- Out-of-memory killer terminating important services
- Disk full on important volumes
- Failed critical backups
- Successful suspicious login
- Repeated brute-force authentication attempts
- Privilege escalation
- Unexpected sudo/root/admin activity
- New admin user created unexpectedly
- Firewall/security rule changes
- IDS/IPS exploit alerts
- Malware, rootkit, suspicious executable activity, or command execution
- Unexpected outbound traffic suggesting compromise
- Access denied events involving secrets, credentials, keys, sensitive files, or system configuration
- Repeated MEDIUM events causing outage, instability, data risk, or security risk

MEDIUM severity means the log indicates a real issue worth investigating, but it does not clearly show immediate outage, compromise, or data-loss risk.

Classify as MEDIUM when the log suggests:

- Service degradation, temporary outage, or self-recovered restart
- API timeout, DNS timeout, high latency, packet loss, or intermittent connectivity
- Container health check failure that later recovers
- Wireless client anomalies such as DNS timeout, poor satisfaction, high retries, roaming problems, or high channel utilization
- Blocked traffic that is unusual, repeated, internal, or from sensitive hosts
- Failed SSH/login attempts that are limited or not clearly successful
- Suspicious but inconclusive firewall, authentication, or outbound network behavior
- AppArmor/SELinux denials that may affect a non-critical service
- Missing config files, permission issues, deprecated settings, certificate warnings, or Docker network warnings
- Resource pressure such as high disk, memory, CPU, temperature, queue buildup, rate limiting, or API throttling
- Recurring LOW events that are noisy enough to indicate misconfiguration, scanning, device malfunction, or operational impact

LOW severity means the log is normal, expected, informational, benign background noise, or a minor issue that does not require action.

Classify as LOW when the log suggests:

- Normal service start/stop
- Successful scheduled job
- Health check passed
- DHCP lease assigned
- DNS query handled normally
- NTP sync completed
- Docker container started successfully
- Expected user login from a known source
- Routine cron activity
- Random inbound internet scanning blocked by firewall
- Expected multicast, broadcast, SSDP, UPnP, mDNS, IGMP, or IoT chatter
- IPv6 DHCP/router advertisement noise that is expected in the environment
- TCP resets with no evidence of service failure
- Informational AP, firewall, kernel, daemon, or container messages
- Single transient timeout
- Optional file missing where no service impact is shown
- AppArmor/SELinux audit events that do not deny important functionality
- Known-safe events from expected devices, hosts, subnets, or rules

Classification Rules

Do not classify based only on the syslog level. A log marked "minor", "warning", or "notice" can still be HIGH if it indicates system failure or security compromise. A log marked "error" can be LOW if it is harmless, expected, or non-impactful.

Escalate severity when:

- The affected system is critical infrastructure
- The same error repeats frequently
- The event involves authentication, authorization, privilege escalation, firewall rules, admin access, secrets, or sensitive files
- The log indicates data loss, corruption, disk failure, service unavailability, or failed backups
- The event affects multiple clients, multiple services, or an entire network segment
- A normally harmless event is happening at high volume
- The event changes from blocked/denied to successful/allowed

De-escalate severity when:

- The traffic was blocked successfully and appears to be routine internet noise
- The source and destination are known benign devices
- The event is isolated and self-recovered
- The message is informational or expected during normal operation
- The denied action involves a non-critical optional file
- The event is common multicast, broadcast, discovery, or IoT chatter
- There is no evidence of user impact, service failure, compromise, or data risk

Important Context Rules

- Blocked firewall traffic is usually LOW unless it is repeated, internal, targeted at sensitive services, or suggests scanning/compromise.
- A denied AppArmor/SELinux access is usually LOW or MEDIUM unless it blocks a critical service or involves credentials, secrets, keys, system config, or protected data.
- Wireless warnings are usually MEDIUM when they affect clients or performance, and HIGH only if they cause widespread outage or critical infrastructure failure.
- Security violations should generally be MEDIUM or HIGH, never LOW, unless clearly benign and expected.
- System failure, storage failure, database failure, or critical network failure should generally be HIGH.
- Repetition matters. A single harmless event may be LOW, but the same event repeated many times may become MEDIUM or HIGH.

Required Output Format

You must return valid JSON only.

Do not wrap the JSON in Markdown.

Do not include comments.

Do not include explanatory text outside the JSON.

Use this exact JSON structure:

{
  "severity": "HIGH | MEDIUM | LOW",
  "category": "Security | System Failure | Service Degradation | Network | Firewall | Authentication | Storage | Docker | Wireless | Database | Configuration | Informational | Unknown",
  "summary": "One short sentence explaining what happened.",
  "reason": "A concise explanation of why this severity was chosen, referencing the important parts of the log.",
  "recommended_action": "A practical next step. For LOW severity, this may be 'No immediate action needed' or 'Suppress if noisy.'",
  "confidence": "High | Medium | Low",
  "escalation_factors": [
    "List factors that increased severity, or use an empty array."
  ],
  "deescalation_factors": [
    "List factors that reduced severity, or use an empty array."
  ],
  "suggested_rule_action": "alert_immediately | alert_on_repeat | store_only | suppress_if_known",
  "deduplication_key": "A short stable key based on source/process/message pattern, suitable for grouping similar logs.",
  "notes": "Mention uncertainty, missing context, or whether repetition would change severity."
}

Now classify the following log entry:

{LOG_ENTRY}"""
