import logging
import os
import re
from datetime import datetime

import requests

from src.core.config import (
    AI_API_BASE_URL,
    AI_API_KEY,
    AI_MODEL,
    AI_SAMPLE_MAX_LINES,
    AI_DISCOVERY_ENABLED,
    MITE_ANALYSIS_DIR,
)
from src.core.db import (
    get_log_samples_for_source,
    insert_ai_analysis,
)
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

SECRET_PATTERNS = [
    re.compile(r"password\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"passwd\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"token\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"api_key\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"authorization:\s*\S+", re.IGNORECASE),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(r"secret\s*=\s*\S+", re.IGNORECASE),
]

AI_PROMPT_TEMPLATE = """You are analyzing syslog samples for a lightweight homelab monitoring tool named Mite.

Your job:

- Identify what device/application these logs likely come from.
- Explain normal vs suspicious vs critical messages.
- Recommend alert rules.
- Avoid over-alerting.
- Prefer high-confidence rules.
- Return a Markdown analysis.
- Include a YAML code block named MITE_rules.

Rules should use this schema:
rules:
  - name:
    enabled:
    severity:
    description:
    match:
      contains_any:
      contains_all:
      regex_any:
      regex_all:
      host_any:
      source_ip_any:
      program_any:
      severity_any:
      facility_any:
    cooldown_seconds:
    cooldown_key:
    discord:
    action:

Do not include secrets.
Do not include private credentials.
Do not recommend alerting on every routine firewall block.
Make rules practical and low-noise.

Analyze these logs:

{log_samples}"""


def redact_secrets(text):
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def run_ai_analysis(source_ip=None, host=None, sample_count=None):
    if not AI_DISCOVERY_ENABLED:
        return {"status": "error", "message": "AI discovery is not enabled"}

    if not AI_API_BASE_URL or not AI_API_KEY:
        return {"status": "error", "message": "AI API not configured"}

    max_lines = sample_count or AI_SAMPLE_MAX_LINES
    samples = get_log_samples_for_source(source_ip=source_ip, host=host, limit=max_lines)

    if not samples:
        return {"status": "error", "message": "No log samples found for this source"}

    # Redact secrets from samples
    redacted_samples = [redact_secrets(s) for s in samples]
    log_text = "\n".join(redacted_samples)

    prompt = AI_PROMPT_TEMPLATE.format(log_samples=log_text)

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

        # Save markdown file
        identifier = host or source_ip or "unknown"
        identifier = re.sub(r"[^\w\-.]", "_", identifier)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{timestamp}-{identifier}.md"
        filepath = os.path.join(MITE_ANALYSIS_DIR, filename)

        os.makedirs(MITE_ANALYSIS_DIR, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(ai_content)

        # Extract summary (first paragraph after # heading)
        summary_match = re.search(r"##\s*Summary\s*\n+(.*?)(?:\n\n|\n##)", ai_content, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""

        analysis_id = insert_ai_analysis(
            created_at=datetime.now().isoformat(),
            source_ip=source_ip,
            host=host,
            sample_count=len(samples),
            markdown_path=filepath,
            status="success",
            summary=summary[:500] if summary else None,
        )

        log_info(logger, f"[INFO] AI analysis saved to {filepath}")
        return {"status": "success", "id": analysis_id, "markdown_path": filepath}

    except Exception as e:
        log_error(logger, f"[ERROR] AI analysis failed: {e}")

        insert_ai_analysis(
            created_at=datetime.now().isoformat(),
            source_ip=source_ip,
            host=host,
            sample_count=len(samples),
            markdown_path="",
            status="failed",
            summary=str(e)[:500],
        )

        return {"status": "error", "message": str(e)}
