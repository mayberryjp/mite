import logging
import time

from src.core.db import (
    get_unprocessed_logs,
    insert_alert,
    mark_logs_processed,
    upsert_host,
    increment_host_alert_count,
    check_cooldown,
    update_cooldown,
    update_alert_discord_sent,
)
from src.core.rule_loader import load_all_rules
from src.core.rule_engine import match_rule, build_cooldown_key
from src.core.discord import send_alert_discord
from src.core.config import DISCORD_WEBHOOK_URL
from src.utils.locallogging import log_error, log_info

logger = logging.getLogger(__name__)


def process_logs():
    rules, errors = load_all_rules()
    if errors:
        for err in errors:
            log_error(logger, f"[ERROR] Rule load error: {err}")

    active_rules = [r for r in rules if r.get("enabled", True)]
    log_info(logger, f"[INFO] Processing with {len(active_rules)} active rules")

    logs = get_unprocessed_logs(limit=500)
    if not logs:
        return

    log_info(logger, f"[INFO] Processing {len(logs)} unprocessed logs")

    processed_ids = []
    for log_entry in logs:
        for rule in active_rules:
            if match_rule(rule, log_entry):
                rule_name = rule.get("name", "unknown")
                severity = rule.get("severity", "info")
                cooldown_seconds = rule.get("cooldown_seconds", 300)
                cooldown_key = build_cooldown_key(rule, log_entry)

                alert_id = insert_alert(
                    created_at=log_entry["received_at"],
                    log_id=log_entry["id"],
                    rule_name=rule_name,
                    severity=severity,
                    host=log_entry.get("host"),
                    source_ip=log_entry.get("source_ip"),
                    message=log_entry["message"],
                    reason=rule.get("description", ""),
                    action=rule.get("action", ""),
                )

                increment_host_alert_count(
                    log_entry.get("host"), log_entry.get("source_ip")
                )

                # Send Discord if enabled and not in cooldown
                if rule.get("discord", False) and DISCORD_WEBHOOK_URL:
                    in_cooldown = check_cooldown(rule_name, cooldown_key, cooldown_seconds)
                    if not in_cooldown:
                        success = send_alert_discord(
                            severity=severity,
                            rule_name=rule_name,
                            host=log_entry.get("host"),
                            source_ip=log_entry.get("source_ip"),
                            timestamp=log_entry["received_at"],
                            message=log_entry["message"],
                            action=rule.get("action", ""),
                        )
                        if success and alert_id:
                            update_alert_discord_sent(alert_id)
                            update_cooldown(rule_name, cooldown_key)

        processed_ids.append(log_entry["id"])

    mark_logs_processed(processed_ids)
    log_info(logger, f"[INFO] Marked {len(processed_ids)} logs as processed")


if __name__ == "__main__":
    log_info(logger, "[INFO] Log processor starting, waiting 10 seconds...")
    time.sleep(10)

    from src.core.db import init_database
    init_database()

    PROCESS_INTERVAL = 10

    while True:
        try:
            process_logs()
        except Exception as e:
            log_error(logger, f"[ERROR] Processor error: {e}")

        time.sleep(PROCESS_INTERVAL)
