"""Capture full AI request/response logs, one file per request.

When the `log_ai_requests` setting is enabled, every outbound AI API call
(classification and the weekly regex efficiency review) is written to its own
JSON file under a dedicated subfolder of MITE_LOGS_DIR.

Security notes:
- The Authorization header (bearer API key) is never written to disk; it is
  redacted by callers before passing the request payload here.
"""

import json
import os
import re
import uuid
from datetime import datetime

from src.core.config import MITE_LOGS_DIR
from src.core.db import get_setting

AI_REQUEST_LOG_SUBFOLDER = "airequests"

_TYPE_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]+")


def _logs_dir():
    return os.path.join(MITE_LOGS_DIR, AI_REQUEST_LOG_SUBFOLDER)


def is_ai_request_logging_enabled():
    """Return True when the `log_ai_requests` setting is enabled."""
    raw = get_setting("log_ai_requests", "false")
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def log_ai_request(
    request_type, request_payload, response_status, response_body, endpoint=None
):
    """Write a single AI request/response pair to its own file when enabled.

    Best-effort: never raises so it can never break classification.
    Returns the created log id (filename without extension), or None.
    """
    try:
        if not is_ai_request_logging_enabled():
            return None

        log_dir = _logs_dir()
        os.makedirs(log_dir, exist_ok=True)

        now = datetime.now()
        ts_compact = now.strftime("%Y%m%dT%H%M%S_%f")
        safe_type = (
            _TYPE_SANITIZE_RE.sub("-", str(request_type)).strip("-") or "request"
        )
        filename = f"{ts_compact}_{safe_type}_{uuid.uuid4().hex[:8]}.json"
        path = os.path.join(log_dir, filename)

        record = {
            "id": filename[:-5],
            "timestamp": now.isoformat(),
            "request_type": request_type,
            "endpoint": endpoint,
            "request": request_payload,
            "response": {
                "status_code": response_status,
                "body": response_body,
            },
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False, default=str)

        return filename[:-5]
    except Exception:
        # AI request logging must never interfere with classification.
        return None
