import json
import logging
import re

import requests

from src.core.ai_request_log import log_ai_request
from src.core.config import AI_API_BASE_URL, AI_API_KEY, AI_MODEL
from src.core.db import (
    create_action,
    get_ai_api_call_count_24h,
    get_all_patterns,
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


REGEX_EFFICIENCY_REVIEW_REQUIREMENTS = r"""
You are a strict regex deduplication auditor.

Analyze this dataset of log classification rules.

Input format:

IDRule NameRegex Pattern

Return ONLY valid JSON. Do not include markdown, comments, explanations, or any prose outside the JSON object.

Your task is narrowly limited.

You are NOT redesigning the rule system.
You are NOT grouping broad log families.
You are NOT creating new parent rules for Docker, Wi-Fi, systemd, firewall, cron, etc.
You are NOT trying to reduce the maximum number of rules.
You are ONLY identifying regexes that are exact duplicates, almost exact duplicates, or obvious near-literal subset/superset overlaps.

Include a candidate only if one of these is true:

1. The regex strings are exactly identical.
2. The regexes differ only by rule name.
3. The regexes differ only by a hard-coded hostname, device name, interface name, username, container ID, veth ID, number, MAC address, IP address, or placeholder token.
4. One regex is an obvious near-literal subset of another, with only one or two extra literal tokens.
5. The regexes have the same ordered literal tokens and differ only by tiny wildcard placement, optional punctuation, or escaping differences.
Exclude candidates if:

1. The patterns merely belong to the same broad log family.
2. The patterns describe different lifecycle states, such as started vs stopped, opened vs closed, accepted vs failed, connected vs disconnected, joined vs left, success vs error.
3. Consolidating them would require a broad alternation.
4. Consolidating them would require operational interpretation instead of regex similarity comparison.
5. The only similarity is shared generic tokens like `error`, `failed`, `docker`, `hostapd`, `cron`, `systemd`, `firewall`, `kernel`, `service`, or `.*`.
6. The replacement would be meaningfully broader than the existing patterns.
7. The proposed match depends on assumptions about alerting, routing, severity, or operational meaning.
Be strict. Prefer returning fewer high-confidence candidates over noisy recommendations.

Use this exact JSON schema:

{
"analysis_type": "strict_regex_duplicate_and_near_duplicate_audit",
"rules_reviewed": 0,
"exact_duplicates": [
{
"duplicate_group_id": "ED001",
"regex": "string",
"rules": [
{
"id": 0,
"name": "string"
}
],
"recommended_action": "keep_one_delete_others",
"recommended_survivor_id": 0,
"safe_to_delete_rule_ids": [0],
"reason": "string",
"confidence": 100
}
],
"near_duplicates": [
{
"near_duplicate_group_id": "ND001",
"rules": [
{
"id": 0,
"name": "string",
"regex": "string",
"role": "generic_survivor | hardcoded_variant | broader_variant | narrower_variant | peer_variant"
}
],
"difference_type": "hostname_only | device_name_only | interface_only | veth_id_only | user_only | numeric_token_only | ip_or_mac_only | placeholder_only | escaping_only | wildcard_placement_only | tiny_literal_difference | near_literal_subset_superset",
"difference_explanation": "string",
"recommended_action": "deduplicate | keep_separate | needs_human_review",
"recommended_survivor_rule_id": 0,
"safe_to_delete_rule_ids": [0],
"proposed_survivor_regex": "string or null",
"risk": "low | medium | high",
"risk_notes": "string",
"confidence": 0
}
],
"shadowing_or_ordering_warnings": [
{
"warning_id": "SW001",
"broad_rule": {
"id": 0,
"name": "string",
"regex": "string"
},
"possibly_shadowed_rules": [
{
"id": 0,
"name": "string",
"regex": "string"
}
],
"reason": "string",
"recommended_action": "check_rule_order | keep_specific_before_broad | delete_specific_if_label_detail_not_needed | needs_human_review",
"risk": "low | medium | high",
"confidence": 0
}
],
"rejected_possible_matches": [
{
"rule_ids": [0],
"reason_rejected": "string"
}
],
"summary": {
"exact_duplicate_groups": 0,
"near_duplicate_groups": 0,
"shadowing_or_ordering_warnings": 0,
"estimated_safe_deletions": 0,
"needs_human_review": 0,
"notes": [
"string"
]
}
}

Additional instructions:

- Do not produce broad replacement regexes.
- Do not merge lifecycle opposites such as start/stop, open/closed, connect/disconnect, join/leave, success/failure.
- Only propose a `proposed_survivor_regex` when it is identical to an existing regex or a minimally normalized version.
- If a broad existing regex already safely covers hard-coded variants, recommend keeping the broad existing regex and deleting only the hard-coded variants.
- If the decision depends on rule ordering, place it in `shadowing_or_ordering_warnings`, not `near_duplicates`.
- If unsure, place the candidate in `rejected_possible_matches` or mark it `needs_human_review`.
- Confidence must be an integer from 0 to 100.
- Keep the output compact but complete.
- Return valid JSON only.
Now analyze the dataset.
"""


def _parse_ai_results(ai_content):
    """Parse AI output into JSON results, with a small repair pass for bad escapes."""
    json_match = re.search(r"\[.*\]", ai_content, re.DOTALL)
    if not json_match:
        raise json.JSONDecodeError(
            "Could not find JSON array in AI response", ai_content, 0
        )

    json_text = json_match.group()
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        # Common model failure: regex-like strings use invalid JSON escapes (e.g., \d, \.).
        if "Invalid \\escape" not in str(e):
            raise

        repaired = _repair_invalid_json_escapes(json_text)
        return json.loads(repaired)


def _repair_invalid_json_escapes(json_text):
    """Escape invalid backslashes inside JSON strings while preserving valid escapes."""
    result = []
    in_string = False
    i = 0
    n = len(json_text)

    while i < n:
        ch = json_text[i]

        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue

        if ch == '"':
            # Count preceding backslashes to determine if quote is escaped.
            backslashes = 0
            j = i - 1
            while j >= 0 and json_text[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 0:
                in_string = False
            result.append(ch)
            i += 1
            continue

        if ch != "\\":
            result.append(ch)
            i += 1
            continue

        # Handle backslash in JSON string.
        if i + 1 >= n:
            result.append("\\\\")
            i += 1
            continue

        nxt = json_text[i + 1]
        if nxt in ['"', "\\", "/", "b", "f", "n", "r", "t"]:
            result.append("\\")
            result.append(nxt)
            i += 2
            continue

        if nxt == "u" and i + 5 < n:
            code = json_text[i + 2 : i + 6]
            if all(c in "0123456789abcdefABCDEF" for c in code):
                result.append("\\")
                result.append("u")
                result.append(code)
                i += 6
                continue

        # Invalid escape sequence; convert '\x' to '\\x'.
        result.append("\\\\")
        i += 1

    return "".join(result)


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

        endpoint = f"{AI_API_BASE_URL.rstrip('/')}/chat/completions"
        request_log = {
            "url": endpoint,
            "method": "POST",
            "headers": {
                "Authorization": "Bearer ***REDACTED***",
                "Content-Type": "application/json",
            },
            "body": payload,
        }

        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=120,
        )

        log_ai_request("classification", request_log, resp.status_code, resp.text)

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


def review_pattern_regex_efficiency():
    """Review all regex-bearing patterns for consolidation opportunities.

    Updates read-only setting key `ai_efficiency_score` and creates action suggestions.
    """
    if not AI_API_BASE_URL or not AI_API_KEY:
        return {
            "status": "error",
            "message": "AI API not configured — set AI_API_BASE_URL, AI_API_KEY, and AI_MODEL",
        }

    patterns, _ = get_all_patterns(limit=None, offset=0)
    regex_patterns = [
        p
        for p in patterns
        if isinstance(p.get("match_regex"), str) and p["match_regex"].strip()
    ]

    if len(regex_patterns) < 2:
        set_setting("ai_efficiency_score", "100.0")
        return {
            "status": "ok",
            "efficiency_score": 100.0,
            "suggestions_created": 0,
            "message": "Not enough regex patterns to evaluate",
        }

    if not _check_rate_limit():
        count = get_ai_api_call_count_24h()
        rate_limit = _get_ai_daily_rate_limit()
        return {
            "status": "error",
            "message": f"Rate limit reached ({count}/{rate_limit})",
        }

    lines = []
    for p in regex_patterns:
        rule_name = p.get("title") or f"pattern_{p.get('id')}"
        lines.append(f"{p.get('id')}\t{rule_name}\t{p.get('match_regex')}")

    review_prompt = (
        "Analyze the following dataset of regex rules.\n\n"
        + REGEX_EFFICIENCY_REVIEW_REQUIREMENTS
        + "\n\nDataset:\nID\tRule Name\tRegex Pattern\n"
        + "\n".join(lines)
    )

    try:
        headers = {
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": AI_MODEL,
            "messages": [{"role": "user", "content": review_prompt}],
        }

        endpoint = f"{AI_API_BASE_URL.rstrip('/')}/chat/completions"
        request_log = {
            "url": endpoint,
            "method": "POST",
            "headers": {
                "Authorization": "Bearer ***REDACTED***",
                "Content-Type": "application/json",
            },
            "body": payload,
        }

        resp = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=120,
        )

        log_ai_request(
            "regex_efficiency_review", request_log, resp.status_code, resp.text
        )

        if resp.status_code != 200:
            return {
                "status": "error",
                "message": f"AI API HTTP {resp.status_code}: {resp.text[:500]}",
            }

        record_ai_api_call()
        data = resp.json()
        ai_content = data["choices"][0]["message"]["content"]

        obj_match = re.search(r"\{.*\}", ai_content, re.DOTALL)
        if not obj_match:
            return {
                "status": "error",
                "message": "Invalid JSON object from AI regex review",
            }

        try:
            review_result = json.loads(obj_match.group())
        except json.JSONDecodeError:
            repaired = _repair_invalid_json_escapes(obj_match.group())
            review_result = json.loads(repaired)

        # Keep backward compatibility if model returns legacy `efficiency_score`.
        score_raw = review_result.get("efficiency_score")
        if score_raw is None:
            summary_obj = review_result.get("summary")
            if not isinstance(summary_obj, dict):
                summary_obj = {}

            rules_reviewed_raw = review_result.get(
                "rules_reviewed", len(regex_patterns)
            )
            safe_deletions_raw = summary_obj.get("estimated_safe_deletions", 0)

            try:
                rules_reviewed = int(rules_reviewed_raw)
            except (TypeError, ValueError):
                rules_reviewed = len(regex_patterns)

            try:
                safe_deletions = int(safe_deletions_raw)
            except (TypeError, ValueError):
                safe_deletions = 0

            if rules_reviewed <= 0:
                score = 100.0
            else:
                score = 100.0 - ((safe_deletions / rules_reviewed) * 100.0)
        else:
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = 0.0

        if score < 0:
            score = 0.0
        if score > 100:
            score = 100.0

        set_setting("ai_efficiency_score", f"{score:.2f}")

        exact_duplicates = review_result.get("exact_duplicates")
        if not isinstance(exact_duplicates, list):
            exact_duplicates = []

        near_duplicates = review_result.get("near_duplicates")
        if not isinstance(near_duplicates, list):
            near_duplicates = []

        # Backward compatibility with previous schema.
        suggestions = review_result.get("suggestions")
        if not isinstance(suggestions, list):
            suggestions = []

        created = 0

        for group in exact_duplicates:
            if not isinstance(group, dict):
                continue

            survivor = group.get("recommended_survivor_id")
            delete_ids = group.get("safe_to_delete_rule_ids") or []
            if not isinstance(delete_ids, list):
                delete_ids = []

            cleaned_delete_ids = []
            for pid in delete_ids:
                try:
                    cleaned_delete_ids.append(int(pid))
                except (TypeError, ValueError):
                    continue

            if not cleaned_delete_ids:
                continue

            action_text = (
                f"Regex exact duplicate suggestion: keep pattern {survivor}, "
                f"consider deleting {cleaned_delete_ids}. Reason: {group.get('reason', '')}"
            )
            create_action(action_text=action_text, acknowledged=False)
            created += 1

        for group in near_duplicates:
            if not isinstance(group, dict):
                continue

            recommended_action = group.get("recommended_action")
            if recommended_action not in ("deduplicate", "needs_human_review"):
                continue

            rules = group.get("rules") or []
            if not isinstance(rules, list):
                rules = []

            rule_ids = []
            for r in rules:
                if not isinstance(r, dict):
                    continue
                try:
                    rule_ids.append(int(r.get("id")))
                except (TypeError, ValueError):
                    continue

            if len(rule_ids) < 2:
                continue

            action_text = (
                f"Regex near-duplicate suggestion for pattern IDs {rule_ids}: "
                f"action={recommended_action}, difference_type={group.get('difference_type', '')}, "
                f"reason={group.get('difference_explanation', '')}"
            )
            create_action(action_text=action_text, acknowledged=False)
            created += 1

        for s in suggestions:
            if not isinstance(s, dict):
                continue

            pattern_ids = s.get("pattern_ids")
            reason = s.get("reason", "")
            recommendation = s.get("recommendation", "")

            if not isinstance(pattern_ids, list) or len(pattern_ids) < 2:
                continue

            cleaned_ids = []
            for pid in pattern_ids:
                try:
                    cleaned_ids.append(int(pid))
                except (TypeError, ValueError):
                    continue

            if len(cleaned_ids) < 2:
                continue

            action_text = (
                f"Regex consolidation suggestion for pattern IDs {cleaned_ids}: "
                f"{recommendation}. Reason: {reason}"
            )
            create_action(action_text=action_text, acknowledged=False)
            created += 1

        return {
            "status": "ok",
            "efficiency_score": score,
            "suggestions_created": created,
            "summary": review_result.get("summary", ""),
            "regex_patterns_reviewed": len(regex_patterns),
        }

    except requests.ConnectionError as e:
        return {"status": "error", "message": f"Connection failed: {e}"}
    except requests.Timeout:
        return {"status": "error", "message": "AI API request timed out"}
    except Exception as e:
        log_error(
            logger,
            f"[ERROR] AI regex efficiency review failed: {type(e).__name__}: {e}",
        )
        return {"status": "error", "message": str(e)}
