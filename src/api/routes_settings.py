import json
import logging

from bottle import request, response

from src.core.models import DEFAULT_AI_PROMPT_TEMPLATE
from src.core.db import get_all_settings, get_setting, set_setting
from src.utils.locallogging import log_error, log_info

EDITABLE_SETTINGS = {
    "ai_prompt_template": {
        "description": "Prompt template sent to the AI for log pattern classification. Use {patterns} as the placeholder for pattern data.",
        "default": DEFAULT_AI_PROMPT_TEMPLATE,
    },
}


def setup_settings_routes(app):

    @app.route("/api/settings", method=["GET"])
    def api_get_settings():
        logger = logging.getLogger(__name__)
        try:
            result = []
            for key, meta in EDITABLE_SETTINGS.items():
                value = get_setting(key)
                result.append({
                    "key": key,
                    "value": value,
                    "default": meta["default"],
                    "is_custom": value is not None,
                    "description": meta["description"],
                })
            response.content_type = "application/json"
            return json.dumps(result)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get settings: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>", method=["GET"])
    def api_get_setting(key):
        logger = logging.getLogger(__name__)
        try:
            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})
            meta = EDITABLE_SETTINGS[key]
            value = get_setting(key)
            response.content_type = "application/json"
            return json.dumps({
                "key": key,
                "value": value,
                "default": meta["default"],
                "is_custom": value is not None,
                "description": meta["description"],
            })
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>", method=["PUT"])
    def api_set_setting(key):
        logger = logging.getLogger(__name__)
        try:
            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})
            body = request.json or {}
            if "value" not in body:
                response.status = 400
                return json.dumps({"error": "Missing required field: value"})
            value = body["value"]
            if not isinstance(value, str) or not value.strip():
                response.status = 400
                return json.dumps({"error": "value must be a non-empty string"})
            set_setting(key, value)
            log_info(logger, f"[INFO] Setting '{key}' updated")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "key": key})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to update setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>/reset", method=["POST"])
    def api_reset_setting(key):
        logger = logging.getLogger(__name__)
        try:
            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})
            # Deleting the row causes get_setting to return None → falls back to default
            from src.core.db import connect_to_db, disconnect_from_db, execute_with_retry
            def _delete():
                conn = connect_to_db()
                if not conn:
                    return
                try:
                    conn.cursor().execute("DELETE FROM settings WHERE key = ?", (key,))
                    conn.commit()
                finally:
                    disconnect_from_db(conn)
            execute_with_retry(_delete)
            log_info(logger, f"[INFO] Setting '{key}' reset to default")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "key": key})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to reset setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})
