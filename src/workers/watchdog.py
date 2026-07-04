"""Watchdog process.

Periodically verifies that every long-running Mite process is alive and that the
API is healthy. Follows the same pattern as sando's watchdog: scan the process
table for each required ``python -m <module>`` command, and hit the API health
endpoint; if the API is repeatedly unhealthy, terminate it so supervisord (which
runs every program with autorestart=true) brings it back.

Faults are surfaced via ``log_error`` (which records an action). To avoid action
spam, a missing-process action is only recorded when the set of missing processes
changes, and the API is only restarted after several consecutive failed checks.
"""

import logging
import time

import psutil
import requests

from src.core.config import MITE_API_PORT
from src.utils.locallogging import log_error, log_info, log_warn

logger = logging.getLogger(__name__)

# Long-running Mite processes (supervisord programs), identified by their
# ``python -m <module>`` command line.
REQUIRED_PROCESSES = [
    "src.api.server",
    "src.workers.udp_listener",
    "src.workers.tcp_listener",
    "src.workers.processor",
    "src.workers.ai_worker",
    "src.workers.retention_worker",
    "src.workers.mcp_server",
]

API_PROCESS = "src.api.server"
API_HEALTH_URL = f"http://localhost:{MITE_API_PORT}/api/health"

SLEEP_INITIAL_SECONDS = 180
CHECK_INTERVAL_SECONDS = 60
# Consecutive failed health checks required before the API is restarted (avoids
# restarting on a single transient blip).
API_UNHEALTHY_RESTART_THRESHOLD = 2

# State carried between cycles so a persistent fault is not re-recorded every loop.
_last_missing = set()
_api_failure_streak = 0


def _matches_module(cmdline, module_name):
    """True if a process command line runs ``python -m <module_name>``."""
    return bool(cmdline) and f"-m {module_name}" in " ".join(cmdline)


def is_process_running(module_name):
    """Return True if a ``python -m <module_name>`` process is currently running."""
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if _matches_module(proc.info.get("cmdline"), module_name):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


def check_processes():
    """Verify every required process is running.

    Records an action (via log_error) only when the set of missing processes
    changes, so a crash-looping process does not create an action every cycle.
    """
    global _last_missing
    missing = {name for name in REQUIRED_PROCESSES if not is_process_running(name)}

    if missing and missing != _last_missing:
        log_error(
            logger,
            f"[ERROR] Missing Mite processes: {', '.join(sorted(missing))}. "
            "Check container health, configuration, and logs.",
        )
    elif missing:
        log_warn(
            logger,
            f"[WARN] Still missing Mite processes: {', '.join(sorted(missing))}",
        )
    elif _last_missing:
        log_info(logger, "[INFO] All required Mite processes are running again")
    else:
        log_info(logger, "[INFO] All required Mite processes are running")

    _last_missing = missing


def _terminate_api():
    """Terminate the API process so supervisord restarts it."""
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if _matches_module(proc.info.get("cmdline"), API_PROCESS):
                log_info(
                    logger,
                    f"[INFO] Terminating API process (PID {proc.pid}); "
                    "supervisord will restart it",
                )
                proc.terminate()
                proc.wait(timeout=10)
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            psutil.TimeoutExpired,
        ):
            continue
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to terminate API process: {e}")


def check_api_health():
    """Hit /api/health; after repeated failures, restart the API process."""
    global _api_failure_streak
    healthy = False
    try:
        resp = requests.get(API_HEALTH_URL, timeout=30)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            healthy = True
        else:
            log_warn(
                logger,
                f"[WARN] API health check failed: HTTP {resp.status_code} "
                f"{resp.text[:200]!r}",
            )
    except requests.RequestException as e:
        log_warn(logger, f"[WARN] API health check error at {API_HEALTH_URL}: {e}")

    if healthy:
        _api_failure_streak = 0
        return

    _api_failure_streak += 1
    if _api_failure_streak >= API_UNHEALTHY_RESTART_THRESHOLD:
        log_error(
            logger,
            f"[ERROR] API health check failed {_api_failure_streak} times; "
            "terminating the API process so supervisord restarts it.",
        )
        _terminate_api()
        _api_failure_streak = 0


if __name__ == "__main__":
    log_info(
        logger,
        f"[INFO] Watchdog starting; waiting {SLEEP_INITIAL_SECONDS}s for other "
        "processes to initialize...",
    )
    time.sleep(SLEEP_INITIAL_SECONDS)
    log_info(logger, f"[INFO] Watchdog monitoring every {CHECK_INTERVAL_SECONDS}s")

    while True:
        try:
            check_processes()
            check_api_health()
        except Exception as e:
            log_error(logger, f"[ERROR] Watchdog error: {type(e).__name__}: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)
