import json
import logging

import requests

from src.core.db import get_setting
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

MAX_DISCORD_LENGTH = 1900


def _discord_enabled():
    value = get_setting("discord_notifications_enabled", "false")
    return str(value).strip().lower() == "true"


def _discord_webhook_url():
    value = get_setting("discord_webhook_url", "")
    return (value or "").strip()


def send_discord_message(content):
    if not _discord_enabled():
        return False

    webhook_url = _discord_webhook_url()
    if not webhook_url:
        return False

    try:
        if len(content) > MAX_DISCORD_LENGTH:
            content = content[:MAX_DISCORD_LENGTH] + "\n... (truncated)"

        payload = {"content": content}
        resp = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            log_info(logger, "[INFO] Discord message sent successfully")
            return True
        else:
            log_error(
                logger,
                f"[ERROR] Discord webhook returned status {resp.status_code}: {resp.text}",
            )
            return False
    except Exception as e:
        log_error(logger, f"[ERROR] Failed to send Discord message: {e}")
        return False


def format_alert_message(
    severity, pattern_text, host, source_ip, timestamp, message, ai_explanation
):
    emoji = (
        "🚨"
        if severity in ("critical", "high")
        else "⚠️" if severity == "medium" else "ℹ️"
    )

    text = f"""{emoji} Mite Alert: {severity.title() if severity else 'Unknown'}

Pattern: {pattern_text}
Host: {host or 'unknown'}
Source: {source_ip or 'unknown'}
Time: {timestamp}

Message:
{message}

AI Assessment:
{ai_explanation or 'Pending AI classification.'}"""

    return text


def send_alert_discord(
    severity, pattern_text, host, source_ip, timestamp, message, ai_explanation
):
    content = format_alert_message(
        severity, pattern_text, host, source_ip, timestamp, message, ai_explanation
    )
    return send_discord_message(content)
