import hashlib
import re


# Replacement patterns ordered from most specific to least specific
NORMALIZERS = [
    # UUIDs
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<UUID>"),
    # MAC addresses
    (re.compile(r"(?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}"), "<MAC>"),
    # IPv6 addresses (simplified)
    (re.compile(r"(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}"), "<IPV6>"),
    # IPv4 addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
    # ISO timestamps (2024-01-15T12:30:45.123Z)
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    # BSD syslog timestamps (Jan 15 12:30:45)
    (re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b"), "<TS>"),
    # Date formats (2024-01-15, 01/15/2024)
    (re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b"), "<DATE>"),
    (re.compile(r"\b\d{2}[-/]\d{2}[-/]\d{4}\b"), "<DATE>"),
    # Hex strings (8+ chars)
    (re.compile(r"\b0x[0-9a-fA-F]{4,}\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<HEX>"),
    # Port numbers after colon (e.g., :8080, :443)
    (re.compile(r":(\d{2,5})\b"), ":<PORT>"),
    # Standalone numbers (PIDs, counts, sizes, etc.)
    (re.compile(r"\b\d+\b"), "<N>"),
]

# Collapse repeated placeholders
COLLAPSE_PATTERNS = [
    (re.compile(r"(<N>\s*[.,]\s*)+<N>"), "<N>"),
    (re.compile(r"(<[A-Z]+>)(\s*\1)+"), r"\1"),
]


def extract_pattern(message):
    """Normalize a log message into a pattern by replacing dynamic values with placeholders."""
    if not message:
        return ""

    pattern = message.strip()

    for regex, replacement in NORMALIZERS:
        pattern = regex.sub(replacement, pattern)

    for regex, replacement in COLLAPSE_PATTERNS:
        pattern = regex.sub(replacement, pattern)

    # Collapse multiple whitespace
    pattern = re.sub(r"\s+", " ", pattern).strip()

    return pattern


def hash_pattern(pattern_text):
    """Create a stable hash for a pattern string."""
    return hashlib.sha256(pattern_text.encode("utf-8")).hexdigest()[:16]
