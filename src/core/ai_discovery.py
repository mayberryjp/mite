import json
import logging
import re

import requests

from src.core.config import (
    AI_API_BASE_URL,
    AI_API_KEY,
    AI_MODEL,
)
from src.core.db import (
    get_pending_patterns,
    record_ai_api_call,
    get_ai_api_call_count_24h,
    update_pattern_classification,
)
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

VALID_CLASSIFICATIONS = {"critical", "high", "medium", "low"}

MAX_AI_CALLS_PER_DAY = 500


def _check_rate_limit():
    """Returns True if we can make another AI call, False if rate limited."""
    count = get_ai_api_call_count_24h()
    return count < MAX_AI_CALLS_PER_DAY


AI_PROMPT_TEMPLATE = """I am an infrastructure engineer whose job is to review and classify logs for a network containing servers, firewalls and network devices. Please help me understand the following logs and whether they are important or not and what the meaning of each log is.

Each log pattern below includes a sample of the original log message. I need you to:

1. Explain what this log pattern means in plain language — what system/service produces it, what event it represents, and whether it indicates a problem.
2. Classify its importance for alerting:
   - "critical": System is down, data loss, security breach (e.g., disk failure, kernel panic, OOM killer)
   - "high": Needs attention soon (e.g., repeated auth failures, service crashes, certificate expiry)
   - "medium": Worth monitoring but not urgent (e.g., unusual service restarts, config warnings)
   - "low": Informational, normal operations, or routine/expected traffic (e.g., scheduled tasks completed, routine state changes, firewall blocks on common scan ports, NTP sync, DHCP renewals)
3. Create a Python-compatible regular expression that robustly matches this type of log message. The regex should:
   - Match the static/structural parts of the log (keywords, field names, program names, log format)
   - Use \\S+ or .+? or \\d+ for dynamic values (IPs, ports, MACs, timestamps, counters, hex values)
   - Be specific enough to only match this type of log, not overly broad
   - Work with re.search() (does not need to match the full line)

Respond ONLY with a JSON array. Each element must have:
- "id": the pattern ID (integer, from the input)
- "classification": one of "critical", "high", "medium", "low"
- "description": 2-4 sentences explaining what this log pattern means, what produces it, and why it matters or doesn't matter. Write as if explaining to a fellow engineer.
- "match_regex": a Python regex string that matches this type of log message
- "title": a short human-readable title describing the TYPE of log event (max 40 characters). The title must describe WHAT is happening, NOT contain any timestamps, dates, IP addresses, hostnames, or specific values from the log. Good examples: "SSH Failed Login Attempt", "Firewall Block Inbound TCP", "Kernel WiFi Disconnect", "Cron Session Open/Close", "DHCP Lease Renewal". Bad examples: "2026-06-13 Log", "192.168.1.1 Connection", "office-ap Event".

Example response:
[
  {{"id": 1, "classification": "high", "title": "SSH Brute Force", "description": "This log indicates repeated failed SSH login attempts, typically produced by the sshd daemon. This usually means someone or a bot is attempting to brute-force credentials on your server. You should investigate the source IP and consider blocking it or ensuring fail2ban is active.", "match_regex": "sshd.*Failed password for \\\\S+ from \\\\S+"}},
  {{"id": 2, "classification": "low", "title": "FW Block TCP", "description": "This is a standard firewall deny log for an incoming connection on a commonly scanned port. These are routine internet background noise from automated scanners and do not indicate a targeted attack. Safe to ignore unless the volume is unusually high.", "match_regex": "filterlog.*match,block,in,\\\\d+,.*,tcp,"}}
]

Patterns to analyze:

{patterns}"""


def test_ai_connection():
    """Test that AI API is reachable and configured. Returns (success, error_message)."""
    if not AI_API_BASE_URL:
        return False, "AI_API_BASE_URL is not set"
    if not AI_API_KEY:
        return False, "AI_API_KEY is not set"
    if not AI_MODEL:
        return False, "AI_MODEL is not set"

    try:
        headers = {
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": AI_MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 10,
        }
        resp = requests.post(
            f"{AI_API_BASE_URL.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            return False, f"AI API returned HTTP {resp.status_code}: {resp.text[:500]}"
        return True, None
    except requests.ConnectionError as e:
        return False, f"Cannot connect to AI API at {AI_API_BASE_URL}: {e}"
    except requests.Timeout:
        return False, f"AI API at {AI_API_BASE_URL} timed out after 30 seconds"
    except Exception as e:
        return False, f"AI API test failed: {e}"


def classify_patterns(patterns):
    if not AI_API_BASE_URL or not AI_API_KEY:
        return {"status": "error", "message": "AI API not configured — set AI_API_BASE_URL, AI_API_KEY, and AI_MODEL"}

    if not _check_rate_limit():
        count = get_ai_api_call_count_24h()
        log_error(logger, f"[ERROR] AI rate limit reached: {count}/{MAX_AI_CALLS_PER_DAY} calls in 24h window. Skipping classification.")
        return {"status": "error", "message": f"Rate limit reached ({MAX_AI_CALLS_PER_DAY} calls/day)"}

    if not patterns:
        return {"status": "ok", "classified": 0}

    # Build the prompt input
    pattern_lines = []
    for p in patterns:
        pattern_lines.append(
            f"ID: {p['id']}\nPattern: {p['pattern_text']}\nSample: {p['sample_message']}\nHost: {p.get('host', 'unknown')}\nProgram: {p.get('program', 'unknown')}\n"
        )
    patterns_text = "\n---\n".join(pattern_lines)

    prompt = AI_PROMPT_TEMPLATE.format(patterns=patterns_text)

    try:
        headers = {
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }

        resp = requests.post(
            f"{AI_API_BASE_URL.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )

        if resp.status_code != 200:
            error_body = resp.text[:500]
            log_error(logger, f"[ERROR] AI API returned HTTP {resp.status_code}: {error_body}")
            return {"status": "error", "message": f"AI API HTTP {resp.status_code}: {error_body}"}

        record_ai_api_call()
        ai_call_count = get_ai_api_call_count_24h()
        log_info(logger, f"[INFO] AI call {ai_call_count}/{MAX_AI_CALLS_PER_DAY} in 24h window")

        data = resp.json()
        ai_content = data["choices"][0]["message"]["content"]

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"\[.*\]", ai_content, re.DOTALL)
        if not json_match:
            log_error(logger, f"[ERROR] Could not parse AI response as JSON: {ai_content[:200]}")
            return {"status": "error", "message": "Could not parse AI response"}

        results = json.loads(json_match.group())

        classified = 0
        for result in results:
            pattern_id = result.get("id")
            classification = result.get("classification", "").lower()
            description = result.get("description", "")
            match_regex = result.get("match_regex", "")
            title = result.get("title", "")[:40] if result.get("title") else None

            if classification not in VALID_CLASSIFICATIONS:
                log_error(logger, f"[ERROR] Invalid classification '{classification}' for pattern {pattern_id}")
                continue

            # Validate the regex compiles
            if match_regex:
                try:
                    re.compile(match_regex)
                except re.error as e:
                    log_error(logger, f"[ERROR] Invalid regex from AI for pattern {pattern_id}: {e}")
                    match_regex = ""

            update_pattern_classification(pattern_id, classification, description, match_regex or None, title)
            classified += 1
            log_info(logger, f"[INFO] Pattern {pattern_id} classified as '{classification}'")

        return {"status": "ok", "classified": classified}

    except requests.ConnectionError as e:
        log_error(logger, f"[ERROR] Cannot connect to AI API at {AI_API_BASE_URL}: {e}")
        return {"status": "error", "message": f"Connection failed: {e}"}
    except requests.Timeout:
        log_error(logger, f"[ERROR] AI API request timed out after 120 seconds")
        return {"status": "error", "message": "AI API request timed out"}
    except json.JSONDecodeError as e:
        log_error(logger, f"[ERROR] AI returned invalid JSON: {e}")
        return {"status": "error", "message": f"Invalid JSON from AI: {e}"}
    except Exception as e:
        log_error(logger, f"[ERROR] AI pattern classification failed: {type(e).__name__}: {e}")
        return {"status": "error", "message": str(e)}


def classify_single_pattern(pattern):
    """Classify a single pattern immediately. Returns the updated pattern dict or None on failure."""
    if not AI_API_BASE_URL or not AI_API_KEY:
        log_error(logger, "[ERROR] AI API not configured — cannot classify pattern. Set AI_API_BASE_URL, AI_API_KEY, and AI_MODEL")
        return None

    result = classify_patterns([pattern])

    if result.get("status") == "error":
        log_error(logger, f"[ERROR] AI classification failed for pattern {pattern['id']}: {result.get('message', 'unknown error')}")
        return None

    if result.get("classified", 0) > 0:
        from src.core.db import get_pattern_by_id
        return get_pattern_by_id(pattern["id"])

    log_error(logger, f"[ERROR] AI returned no classification for pattern {pattern['id']}")
    return None
