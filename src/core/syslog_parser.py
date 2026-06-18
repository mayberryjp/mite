import re
from datetime import datetime

# RFC 3164 style: <PRI>TIMESTAMP HOSTNAME APP-NAME[PID]: MESSAGE
# Also handles plain text lines with no structure
SYSLOG_RE = re.compile(
    r"^(?:<(\d{1,3})>)?"  # optional PRI
    r"\s*"
    r"(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})?"  # optional BSD timestamp
    r"\s*"
    r"(\S+)?"  # optional hostname
    r"\s+"
    r"(\S+?)?"  # optional program
    r"(?:\[(\d+)\])?"  # optional [pid]
    r":\s*"
    r"(.*)"  # message
)

FACILITY_MAP = {
    0: "kern",
    1: "user",
    2: "mail",
    3: "daemon",
    4: "auth",
    5: "syslog",
    6: "lpr",
    7: "news",
    8: "uucp",
    9: "cron",
    10: "authpriv",
    11: "ftp",
    16: "local0",
    17: "local1",
    18: "local2",
    19: "local3",
    20: "local4",
    21: "local5",
    22: "local6",
    23: "local7",
}

SEVERITY_MAP = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "err",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


def parse_syslog_message(raw_line, source_ip=None):
    """Parse a syslog message tolerantly. Never returns None."""
    raw_line = raw_line.strip()
    if not raw_line:
        return {
            "received_at": datetime.now().isoformat(),
            "source_ip": source_ip,
            "host": None,
            "facility": None,
            "severity": None,
            "program": None,
            "pid": None,
            "message": "",
            "raw_message": raw_line,
        }

    facility = None
    severity = None
    host = None
    program = None
    pid = None
    message = raw_line

    match = SYSLOG_RE.match(raw_line)
    if match:
        pri_str, timestamp_str, hostname, prog, pid_str, msg = match.groups()

        if pri_str:
            try:
                pri = int(pri_str)
                facility = FACILITY_MAP.get(pri >> 3, str(pri >> 3))
                severity = SEVERITY_MAP.get(pri & 7, str(pri & 7))
            except ValueError:
                pass

        host = hostname
        program = prog
        pid = pid_str
        if msg:
            message = msg
    return {
        "received_at": datetime.now().isoformat(),
        "source_ip": source_ip,
        "host": host,
        "facility": facility,
        "severity": severity,
        "program": program,
        "pid": pid,
        "message": message,
        "raw_message": raw_line,
    }
