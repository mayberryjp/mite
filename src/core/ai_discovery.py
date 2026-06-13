import json
import logging
import re

import requests

from src.core.config import (
    AI_API_BASE_URL,
    AI_API_KEY,
    AI_MODEL,
    AI_DISCOVERY_ENABLED,
)
from src.core.db import (
    get_pending_patterns,
    update_pattern_classification,
)
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

VALID_CLASSIFICATIONS = {"critical", "high", "medium", "low", "noise"}

AI_PROMPT_TEMPLATE = """I am an infrastructure engineer whose job is to review and classify logs for a network containing servers, firewalls and network devices. Please help me understand the following logs and whether they are important or not and what the meaning of each log is.

Each log pattern below has dynamic values (IPs, timestamps, numbers) replaced with placeholders like <IP>, <N>, <TS>, etc. I need you to:

1. Explain what this log pattern means in plain language — what system/service produces it, what event it represents, and whether it indicates a problem.
2. Classify its importance for alerting:
   - "critical": System is down, data loss, security breach (e.g., disk failure, kernel panic, OOM killer)
   - "high": Needs attention soon (e.g., repeated auth failures, service crashes, certificate expiry)
   - "medium": Worth monitoring but not urgent (e.g., unusual service restarts, config warnings)
   - "low": Informational, normal operations (e.g., scheduled tasks completed, routine state changes)
   - "noise": Routine/expected traffic, not worth alerting on (e.g., firewall blocks on common scan ports, NTP sync, DHCP renewals)

Respond ONLY with a JSON array. Each element must have:
- "id": the pattern ID (integer, from the input)
- "classification": one of "critical", "high", "medium", "low", "noise"
- "description": 2-4 sentences explaining what this log pattern means, what produces it, and why it matters or doesn't matter. Write as if explaining to a fellow engineer.

Example response:
[
  {{"id": 1, "classification": "high", "description": "This log indicates repeated failed SSH login attempts, typically produced by the sshd daemon. This usually means someone or a bot is attempting to brute-force credentials on your server. You should investigate the source IP and consider blocking it or ensuring fail2ban is active."}},
  {{"id": 2, "classification": "noise", "description": "This is a standard firewall deny log for an incoming connection on a commonly scanned port. These are routine internet background noise from automated scanners and do not indicate a targeted attack. Safe to ignore unless the volume is unusually high."}}
]

Patterns to analyze:

{patterns}"""


def classify_patterns(patterns):
    if not AI_DISCOVERY_ENABLED:
        return {"status": "error", "message": "AI discovery is not enabled"}

    if not AI_API_BASE_URL or not AI_API_KEY:
        return {"status": "error", "message": "AI API not configured"}

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
        resp.raise_for_status()

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

            if classification not in VALID_CLASSIFICATIONS:
                log_error(logger, f"[ERROR] Invalid classification '{classification}' for pattern {pattern_id}")
                continue

            update_pattern_classification(pattern_id, classification, description)
            classified += 1
            log_info(logger, f"[INFO] Pattern {pattern_id} classified as '{classification}'")

        return {"status": "ok", "classified": classified}

    except Exception as e:
        log_error(logger, f"[ERROR] AI pattern classification failed: {e}")
        return {"status": "error", "message": str(e)}
