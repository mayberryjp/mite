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

REGEX STRATEGY — MUST FOLLOW EXACTLY:

Write BROAD, KEYWORD-ANCHORED regexes. Do NOT try to match the full log line token by token.

STEP 1 — Pick 2 to 4 stable keywords from the sample that uniquely identify this event type.
    Good keywords: daemon name, action verb, event name, protocol name, stable field label.
    Bad keywords: anything that looks dynamic — token placeholders (NUMBER, IP_ADDRESS, MAC_ADDRESS,
        HEX_VALUE, TIMESTAMP, DATE, TIME, VERSION, DYNAMIC_VALUE), hostnames, paths with version numbers.
    CRITICAL: Select keywords in the order they appear in the sample message. Left-to-right order matters. NEVER reorder.
    CRITICAL: Every keyword must be copied from literal text that already exists in the sample. Do NOT invent, infer, summarize, or normalize new words.

STEP 2 — Join those keywords with .* between them, preserving the left-to-right order from the sample.
    The result matches any log containing those keywords in the same order, regardless of what is between them.

STEP 3 — Escape regex metacharacters in the keywords (brackets, dots, parens, etc.).

EXAMPLES (follow this pattern exactly):
    sample: 'NUMBER:NUMBER+NUMBER:NUMBER FIREWALL_HOST dhcp6c NUMBER - - Sending Solicit'
    keywords: dhcp6c, Sending Solicit
    regex: 'dhcp6c.*Sending Solicit'

    sample: 'hostapd NUMBER: wifi0ap1: STA MAC_ADDRESS IEEE VERSION: disassociated'
    keywords: hostapd, STA, disassociated
    regex: 'hostapd.*STA.*disassociated'

    sample: 'pam_unix(cron:session): session opened for user root(uid=NUMBER) by root'
    keywords: pam_unix, cron:session, session opened
    regex: 'pam_unix.*cron:session.*session opened'

    sample: 'sshd NUMBER: Failed password for NUMBER from IP_ADDRESS'
    keywords: sshd, Failed password
    regex: 'sshd.*Failed password'

RULES:
- NEVER reconstruct the full log line as a regex.
- NEVER reorder keywords. If the sample reads 'Starting motd-news.service', write 'Starting.*motd-news\.service', not 'motd-news\.service.*Starting'.
- NEVER add words to the regex that do not appear in the sample message. If the sample contains 'msg="stopping restart-manager"', write 'stopping.*restart-manager' and not 'dockerd.*stopping.*restart-manager' unless 'dockerd' appears in the sample itself.
- NEVER use \d+, [0-9]+, [0-9a-fA-F]+, [0-9.]+, or similar patterns in place of token words.
- NEVER convert these token names into patterns: NUMBER, VERSION, IP_ADDRESS, MAC_ADDRESS,
    HEX_VALUE, TIMESTAMP, DATE, TIME, DYNAMIC_VALUE. They are plain words — skip them when picking keywords.
- Do NOT hardcode site-specific hostnames or environment labels.
- Use .* (not .+) between keywords so empty spans are allowed.
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


def _render_prompt_template(prompt_template, patterns_text):
    """Safely render only the {patterns} token without interpreting other braces."""
    if "{patterns}" not in prompt_template:
        # Backward-compatible fallback: append pattern payload when token is missing.
        return f"{prompt_template}\n\nPatterns to analyze:\n\n{patterns_text}"
    return prompt_template.replace("{patterns}", patterns_text)


def preprocess_sample_for_ai(sample_message):
    """
    Apply user-managed regex tokenization rules so AI focuses on structure.
    Rules come from ai_custom_tokens and are applied in order.

    Example:
    "192.168.1.1 firewall[1234]: 0xDEADBEEF"
    -> "IP_ADDRESS firewall[NUMBER]: HEX_VALUE"
    """
    try:
        text = sample_message or ""

        # Apply user-managed regex tokenization rules in order.
        raw_custom = get_setting("ai_custom_tokens") or "[]"
        try:
            custom_tokens = json.loads(raw_custom)
            if not isinstance(custom_tokens, list):
                raise TypeError("ai_custom_tokens must be a JSON array")

            for index, entry in enumerate(custom_tokens):
                if (
                    not isinstance(entry, list)
                    or len(entry) != 2
                    or not isinstance(entry[0], str)
                    or not isinstance(entry[1], str)
                    or not entry[0]
                    or not entry[1]
                ):
                    continue

                try:
                    text = re.sub(entry[0], entry[1], text)
                except re.error as e:
                    log_error(
                        logger,
                        f"[ERROR] Invalid ai_custom_tokens regex at index {index}: {e}",
                    )
        except (json.JSONDecodeError, TypeError):
            log_error(logger, "[ERROR] ai_custom_tokens is not valid JSON; skipping.")

        return text
    except Exception as e:
        log_error(
            logger,
            f"[ERROR] Preprocessing failed: {e}. Using original sample.",
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
        # Preprocess sample message to mask dynamic values so AI focuses on keywords/structure.
        sample_message = p.get("sample_message", "")
        if p.get("sample_is_preprocessed"):
            preprocessed_sample = sample_message
        else:
            preprocessed_sample = preprocess_sample_for_ai(sample_message)

        retry_feedback = p.get("retry_feedback")
        if retry_feedback:
            pattern_lines.append(
                f"ID: {p['id']}\nPattern: {p['pattern_text']}\nSample: {preprocessed_sample}\nHost: {p.get('host', 'unknown')}\nProgram: {p.get('program', 'unknown')}\nRetry Feedback: {retry_feedback}\n"
            )
        else:
            pattern_lines.append(
                f"ID: {p['id']}\nPattern: {p['pattern_text']}\nSample: {preprocessed_sample}\nHost: {p.get('host', 'unknown')}\nProgram: {p.get('program', 'unknown')}\n"
            )
    patterns_text = "\n---\n".join(pattern_lines)

    prompt_template = get_setting("ai_prompt_template") or DEFAULT_AI_PROMPT_TEMPLATE
    prompt = _render_prompt_template(prompt_template, patterns_text)

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
