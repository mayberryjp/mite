import json
import logging

from bottle import Bottle, request, response

from src.core.rule_loader import load_all_rules
from src.utils.locallogging import log_error, log_info


def setup_rules_routes(app):

    @app.route("/api/rules", method=["GET"])
    def api_get_rules():
        logger = logging.getLogger(__name__)
        try:
            rules, errors = load_all_rules()

            result = []
            for rule in rules:
                result.append({
                    "name": rule.get("name"),
                    "enabled": rule.get("enabled", True),
                    "severity": rule.get("severity"),
                    "description": rule.get("description"),
                    "source_file": rule.get("source_file", ""),
                    "cooldown_seconds": rule.get("cooldown_seconds", 300),
                    "discord": rule.get("discord", False),
                    "load_status": "ok",
                })

            for error in errors:
                result.append({
                    "name": error.get("file", "unknown"),
                    "enabled": False,
                    "severity": None,
                    "description": error.get("error", "Load error"),
                    "source_file": error.get("file", ""),
                    "cooldown_seconds": 0,
                    "discord": False,
                    "load_status": "error",
                })

            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(rules)} rules, {len(errors)} errors")
            return json.dumps(result)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get rules: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/rules/reload", method=["POST"])
    def api_reload_rules():
        logger = logging.getLogger(__name__)
        try:
            rules, errors = load_all_rules()
            response.content_type = "application/json"
            log_info(logger, f"[INFO] Reloaded rules: {len(rules)} loaded, {len(errors)} errors")
            return json.dumps({
                "loaded": len(rules),
                "errors": len(errors),
                "error_details": errors,
            })
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to reload rules: {e}")
            response.status = 500
            return {"error": str(e)}
