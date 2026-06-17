import json
import logging
import re

import requests

from src.core.config import AI_API_BASE_URL, AI_API_KEY, AI_MODEL
from src.core.db import (
    get_ai_api_call_count_24h,
    get_setting,
    record_ai_api_call,
    set_setting,
    update_pattern_classification,
)
from src.core.models import DEFAULT_AI_PROMPT_TEMPLATE
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)

VALID_CLASSIFICATIONS = {"high", "medium", "low"}
DEFAULT_AI_DAILY_RATE_LIMIT = 500

STRICT_JSON_REQUIREMENTS = r"""

CRITICAL RESPONSE FORMAT REQUIREMENTS (MUST FOLLOW EXACTLY):
- Respond with ONLY a valid JSON array. No markdown, no code fences, no comments, no prose.
- The first character must be '[' and the last character must be ']'.
- Use strict JSON (RFC 8259):
    - Double quotes for all keys and string values
    - No trailing commas
    - No single-quoted strings
    - Regex strings MUST use JSON-escaped backslashes (example: \\d+, \\S+, \\.)
    - NEVER use invalid JSON escapes like \d, \S, \w, \s, or \. directly
- Every array element must include exactly these keys:
    - "id" (integer)
    - "classification" ("high" | "medium" | "low")
    - "description" (string)
    - "match_regex" (string)
    - "title" (string, max 40 chars)

Example valid output:
[
    {
        "id": 1,
        "classification": "high",
        "description": "This pattern indicates repeated authentication failures from sshd and should be investigated quickly.",
        "match_regex": "sshd.*Failed password for \\\\S+ from \\\\S+",
        "title": "SSH Failed Login"
    }
]
"""


REGEX_GENERALIZATION_REQUIREMENTS = r"""

REGEX QUALITY REQUIREMENTS (MUST FOLLOW EXACTLY):
- Prioritize portability across different environments and site names.
- Treat hostnames/FQDNs as dynamic values unless a specific hostname is the event identity.
- Do NOT hardcode site-specific segments (for example: "mayberry", "corp", "prod", "lab").
- For host tokens, prefer broad hostname patterns such as:\n  - [A-Za-z0-9._-]+\n  - [A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+
- For ANY numeric sequence that MAY contain dots (versions, protocols, IP segments), use [0-9]+(?:[.][0-9]+)* or [0-9.]+ instead of [0-9]+ alone. This includes: product versions (6.8.2), protocol versions (HTTP/1.1, TLS1.3), firmware versions, dotted-quad IP segments, etc.
- For hex values (example: 0xABCD), use [0-9a-fA-F]+ instead of [^\s]+ or \S+ (which are too greedy and will consume commas and other delimiters).
- For CSV fields: if a field can be empty, represent empty as ,, not ,\,,. Use [^,]* for optional values.
- For JSON string values (e.g., inside {"key":"value"}), use [^"]+ to match the value content, NOT \S+ (which includes the closing quote and breaks the JSON structure).
- Keep only truly stable service/event keywords literal (for example: daemon path, action phrase, protocol verb).
- Do NOT over-constrain optional suffixes like domain depth, TLD, minor version, or local naming conventions.
- If sample lines differ only by hostname/site labels, generated regex MUST match all of them.
- NEVER use [^\s]+ or \S+ for structured/bounded values; use specific character classes like [0-9a-fA-F]+, [A-Za-z0-9-]+, [^"]+, etc.

Critical Examples (MUST apply these patterns):
- Bad (misses dots): U6-Pro-[0-9]+\+[0-9]+  matches "U6-Pro-6" but FAILS on "U6-Pro-6.8.2+15592"
- Better (handles all versions): U6-Pro-[0-9]+(?:\.[0-9]+)*\+[0-9]+  matches "U6-Pro-6.8.2+15592" correctly
- Bad (greedy in JSON): {"mac":"\S+","vap":"  will incorrectly match into the next field
- Better (stops at quote): {"mac":"[^"]+","vap":"  only captures the actual MAC value
- Bad (hardcoded hostname): firewall\.farm\.mayberry\.farm
- Better (portable): firewall\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*
- Bad (misses dotted version): IEEE [0-9]+: disassociated
- Better (handles all): IEEE [0-9.]+: disassociated or IEEE [0-9]+(?:\.[0-9]+)*: disassociated
"""


def _parse_ai_results(ai_content):
    """Parse AI output into JSON results using strict JSON decoding only."""
    json_match = re.search(r"\[.*\]", ai_content, re.DOTALL)
    if not json_match:
        raise json.JSONDecodeError(
            "Could not find JSON array in AI response", ai_content, 0
        )
    return json.loads(json_match.group())


def _get_ai_daily_rate_limit():
    """Return the configured AI call limit in a rolling 24-hour window."""
    raw_limit = get_setting("ai_api_daily_rate_limit")
    if raw_limit is None:
        raw_limit = str(DEFAULT_AI_DAILY_RATE_LIMIT)
        set_setting("ai_api_daily_rate_limit", raw_limit)
    try:
        parsed_limit = int(raw_limit)
    except (TypeError, ValueError):
        return DEFAULT_AI_DAILY_RATE_LIMIT

    if parsed_limit < 1:
        return DEFAULT_AI_DAILY_RATE_LIMIT

    return parsed_limit


def _check_rate_limit():
    """Returns True if we can make another AI call, False if rate limited."""
    count = get_ai_api_call_count_24h()
    return count < _get_ai_daily_rate_limit()


def _preprocess_sample_for_ai(sample_message):
    """
    Apply preprocessing regex to sample message to mask/strip dynamic values.
    This helps AI focus on structural patterns instead of specific values.
    Example: "192.168.1.1 firewall[1234]: 0xDEADBEEF" -> "<X> firewall[<X>]: <X>"
    """
    preprocessing_regex = get_setting("ai_sample_preprocessing_regex") or (
        r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2})+|0x[0-9a-fA-F]+|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}|\b\d+\b"
    )

    try:
        return re.sub(preprocessing_regex, "<X>", sample_message)
    except re.error as e:
        log_error(
            logger,
            f"[ERROR] Invalid preprocessing regex: {e}. Using original sample.",
        )
        return sample_message


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
        return {
            "status": "error",
            "message": "AI API not configured — set AI_API_BASE_URL, AI_API_KEY, and AI_MODEL",
        }

    if not _check_rate_limit():
        count = get_ai_api_call_count_24h()
        rate_limit = _get_ai_daily_rate_limit()
        log_error(
            logger,
            f"[ERROR] AI rate limit reached: {count}/{rate_limit} calls in 24h window. Skipping classification.",
        )
        return {
            "status": "error",
            "message": f"Rate limit reached ({rate_limit} calls/day)",
        }

    if not patterns:
        return {"status": "ok", "classified": 0}

    # Build the prompt input
    pattern_lines = []
    for p in patterns:
        # Preprocess sample message to mask dynamic values (IPs, timestamps, hex, MACs, numbers, etc.)
        # so AI focuses on structural patterns instead of specific values
        preprocessed_sample = _preprocess_sample_for_ai(p["sample_message"])
        pattern_lines.append(
            f"ID: {p['id']}\nPattern: {p['pattern_text']}\nSample: {preprocessed_sample}\nHost: {p.get('host', 'unknown')}\nProgram: {p.get('program', 'unknown')}\n"
        )
    patterns_text = "\n---\n".join(pattern_lines)

    prompt_template = get_setting("ai_prompt_template") or DEFAULT_AI_PROMPT_TEMPLATE
    prompt = (
        prompt_template.format(patterns=patterns_text)
        + STRICT_JSON_REQUIREMENTS
        + REGEX_GENERALIZATION_REQUIREMENTS
    )

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
            log_error(
                logger, f"[ERROR] AI API returned HTTP {resp.status_code}: {error_body}"
            )
            return {
                "status": "error",
                "message": f"AI API HTTP {resp.status_code}: {error_body}",
            }

        record_ai_api_call()
        ai_call_count = get_ai_api_call_count_24h()
        rate_limit = _get_ai_daily_rate_limit()
        log_info(logger, f"[INFO] AI call {ai_call_count}/{rate_limit} in 24h window")

        data = resp.json()
        ai_content = data["choices"][0]["message"]["content"]

        try:
            results = _parse_ai_results(ai_content)
        except json.JSONDecodeError as e:
            log_error(logger, f"[ERROR] AI returned invalid JSON: {e}")
            return {"status": "error", "message": f"Invalid JSON from AI: {e}"}

        classified = 0
        for result in results:
            pattern_id = result.get("id")
            classification = result.get("classification", "").lower()
            description = result.get("description", "")
            match_regex = result.get("match_regex", "")
            title = result.get("title", "")[:40] if result.get("title") else None

            if classification == "critical":
                log_info(
                    logger,
                    f"[INFO] AI returned 'critical' for pattern {pattern_id}; downgrading to 'high'",
                )
                classification = "high"

            if classification not in VALID_CLASSIFICATIONS:
                log_error(
                    logger,
                    f"[ERROR] Invalid classification '{classification}' for pattern {pattern_id}",
                )
                continue

            # Validate the regex compiles
            if match_regex:
                try:
                    re.compile(match_regex)
                except re.error as e:
                    log_error(
                        logger,
                        f"[ERROR] Invalid regex from AI for pattern {pattern_id}: {e}",
                    )
                    match_regex = ""

            update_pattern_classification(
                pattern_id, classification, description, match_regex or None, title
            )
            classified += 1
            log_info(
                logger, f"[INFO] Pattern {pattern_id} classified as '{classification}'"
            )

        return {"status": "ok", "classified": classified}

    except requests.ConnectionError as e:
        log_error(logger, f"[ERROR] Cannot connect to AI API at {AI_API_BASE_URL}: {e}")
        return {"status": "error", "message": f"Connection failed: {e}"}
    except requests.Timeout:
        log_error(logger, "[ERROR] AI API request timed out after 120 seconds")
        return {"status": "error", "message": "AI API request timed out"}
    except Exception as e:
        log_error(
            logger, f"[ERROR] AI pattern classification failed: {type(e).__name__}: {e}"
        )
        return {"status": "error", "message": str(e)}


def classify_single_pattern(pattern):
    """Classify a single pattern immediately. Returns the updated pattern dict or None on failure."""
    if not AI_API_BASE_URL or not AI_API_KEY:
        log_error(
            logger,
            "[ERROR] AI API not configured — cannot classify pattern. Set AI_API_BASE_URL, AI_API_KEY, and AI_MODEL",
        )
        return None

    result = classify_patterns([pattern])

    if result.get("status") == "error":
        log_error(
            logger,
            f"[ERROR] AI classification failed for pattern {pattern['id']}: {result.get('message', 'unknown error')}",
        )
        return None

    if result.get("classified", 0) > 0:
        from src.core.db import get_pattern_by_id

        return get_pattern_by_id(pattern["id"])

    log_error(
        logger, f"[ERROR] AI returned no classification for pattern {pattern['id']}"
    )
    return None
