import re
import logging

from src.utils.locallogging import log_debug

logger = logging.getLogger(__name__)


def match_rule(rule, log_entry):
    if not rule.get("enabled", True):
        return False

    match_block = rule.get("match", {})
    if not match_block:
        return False

    message = log_entry.get("message", "")
    host = log_entry.get("host", "") or ""
    source_ip = log_entry.get("source_ip", "") or ""
    program = log_entry.get("program", "") or ""
    severity = log_entry.get("severity", "") or ""
    facility = log_entry.get("facility", "") or ""

    # contains_any - at least one must match
    contains_any = match_block.get("contains_any", [])
    if contains_any:
        if not any(s.lower() in message.lower() for s in contains_any if s):
            return False

    # contains_all - all must match
    contains_all = match_block.get("contains_all", [])
    if contains_all:
        if not all(s.lower() in message.lower() for s in contains_all if s):
            return False

    # regex_any - at least one regex must match
    regex_any = match_block.get("regex_any", [])
    if regex_any:
        if not any(re.search(pattern, message, re.IGNORECASE) for pattern in regex_any if pattern):
            return False

    # regex_all - all regexes must match
    regex_all = match_block.get("regex_all", [])
    if regex_all:
        if not all(re.search(pattern, message, re.IGNORECASE) for pattern in regex_all if pattern):
            return False

    # host_any
    host_any = match_block.get("host_any", [])
    if host_any:
        if not any(h.lower() == host.lower() for h in host_any if h):
            return False

    # source_ip_any
    source_ip_any = match_block.get("source_ip_any", [])
    if source_ip_any:
        if not any(s == source_ip for s in source_ip_any if s):
            return False

    # program_any
    program_any = match_block.get("program_any", [])
    if program_any:
        if not any(p.lower() == program.lower() for p in program_any if p):
            return False

    # severity_any
    severity_any = match_block.get("severity_any", [])
    if severity_any:
        if not any(s.lower() == severity.lower() for s in severity_any if s):
            return False

    # facility_any
    facility_any = match_block.get("facility_any", [])
    if facility_any:
        if not any(f.lower() == facility.lower() for f in facility_any if f):
            return False

    return True


def build_cooldown_key(rule, log_entry):
    cooldown_key_type = rule.get("cooldown_key", "rule_only")
    rule_name = rule.get("name", "unknown")
    host = log_entry.get("host", "") or ""
    source_ip = log_entry.get("source_ip", "") or ""
    message = log_entry.get("message", "") or ""

    if cooldown_key_type == "rule_only":
        return rule_name
    elif cooldown_key_type == "rule_host":
        return f"{rule_name}:{host}"
    elif cooldown_key_type == "rule_host_message":
        return f"{rule_name}:{host}:{message[:100]}"
    elif cooldown_key_type == "rule_source_ip":
        return f"{rule_name}:{source_ip}"
    elif cooldown_key_type == "rule_source_ip_message":
        return f"{rule_name}:{source_ip}:{message[:100]}"
    else:
        return rule_name
