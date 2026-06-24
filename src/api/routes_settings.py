import json
import logging

from bottle import request, response

from src.core.db import delete_setting, get_setting, set_setting
from src.core.models import DEFAULT_AI_CUSTOM_TOKENS, DEFAULT_AI_PROMPT_TEMPLATE
from src.utils.locallogging import log_error, log_info

EDITABLE_SETTINGS = {
    "ai_prompt_template": {
        "description": "Prompt template sent to the AI for log pattern classification. Use {patterns} as the placeholder for pattern data.",
        "default": DEFAULT_AI_PROMPT_TEMPLATE,
        "type": "string",
        "allow_empty": False,
    },
    "ai_custom_tokens": {
        "description": 'User-managed tokenization rules applied in order using regex substitution. JSON array of ["regex_pattern", "TOKEN_NAME"] pairs. Example: [["\\\\b(?:\\\\d{1,3}\\\\.){3}\\\\d{1,3}\\\\b", "IP_ADDRESS"], ["firewall\\\\.office\\\\.example\\\\.com", "FIREWALL_HOST"]]',
        "default": DEFAULT_AI_CUSTOM_TOKENS,
        "type": "json_list_of_pairs",
        "allow_empty": True,
    },
    "min_message_length": {
        "description": "Minimum log message length required before the processor treats a message as meaningful.",
        "default": "50",
        "type": "int",
        "min": 0,
    },
    "discord_notifications_enabled": {
        "description": "Enable or disable Discord alert notifications.",
        "default": "false",
        "type": "bool",
    },
    "discord_webhook_url": {
        "description": "Discord webhook URL used when Discord notifications are enabled.",
        "default": "",
        "type": "string",
        "allow_empty": True,
    },
    "action_on_new_patterns": {
        "description": "Create an action when a new pattern is discovered.",
        "default": "true",
        "type": "bool",
    },
    "notify_on_new_patterns": {
        "description": "Send a Discord notification when a new pattern is discovered.",
        "default": "false",
        "type": "bool",
    },
    "action_on_no_logs": {
        "description": "Create an action when no logs were received in the last 24 hours.",
        "default": "true",
        "type": "bool",
    },
    "notify_on_no_logs": {
        "description": "Send a Discord notification when no logs were received in the last 24 hours.",
        "default": "false",
        "type": "bool",
    },
    "log_retention_days": {
        "description": "How many days of logs to retain before cleanup.",
        "default": "14",
        "type": "int",
        "min": 1,
    },
    "alert_retention_days": {
        "description": "How many days of alerts to retain before cleanup.",
        "default": "30",
        "type": "int",
        "min": 1,
    },
    "ai_api_daily_rate_limit": {
        "description": "Maximum number of AI API classification calls allowed in a rolling 24-hour window.",
        "default": "500",
        "type": "int",
        "min": 1,
    },
    "ai_discovery_interval_seconds": {
        "description": "How often the AI worker polls pending patterns.",
        "default": "3600",
        "type": "int",
        "min": 1,
    },
    "ai_batch_size": {
        "description": "Number of pending patterns to classify per AI worker cycle.",
        "default": "20",
        "type": "int",
        "min": 1,
    },
    "ai_regex_review_interval_seconds": {
        "description": "How often the AI worker reviews regex duplication/similarity for consolidation suggestions.",
        "default": "604800",
        "type": "int",
        "min": 3600,
    },
    "processor_interval_seconds": {
        "description": "How often the processor runs each cycle.",
        "default": "10",
        "type": "int",
        "min": 1,
    },
    "processor_fetch_limit": {
        "description": "Maximum unprocessed logs fetched by the processor per cycle.",
        "default": "100",
        "type": "int",
        "min": 1,
    },
    "retention_check_interval_seconds": {
        "description": "How often the retention worker runs cleanup.",
        "default": "3600",
        "type": "int",
        "min": 1,
    },
    "udp_batch_size": {
        "description": "UDP listener flush batch size.",
        "default": "500",
        "type": "int",
        "min": 1,
    },
    "udp_batch_flush_interval_seconds": {
        "description": "UDP listener flush interval in seconds.",
        "default": "1.0",
        "type": "float",
        "min": 0.1,
    },
    "udp_recv_buffer_bytes": {
        "description": "Requested UDP socket receive buffer size in bytes.",
        "default": "4194304",
        "type": "int",
        "min": 65536,
    },
    "tcp_batch_size": {
        "description": "TCP listener flush batch size per connection.",
        "default": "500",
        "type": "int",
        "min": 1,
    },
    "tcp_batch_flush_interval_seconds": {
        "description": "TCP listener flush interval in seconds.",
        "default": "1.0",
        "type": "float",
        "min": 0.1,
    },
    "regex_cache_ttl_seconds": {
        "description": "How long processor regex cache is kept before refresh.",
        "default": "60",
        "type": "int",
        "min": 1,
    },
    "write_application_log": {
        "description": "Write application logs to daily files under applogs when enabled.",
        "default": "false",
        "type": "bool",
    },
    "write_syslog_log": {
        "description": "Write inbound non-noise syslogs to daily files under syslogs when enabled.",
        "default": "false",
        "type": "bool",
    },
    "log_ai_requests": {
        "description": "Log every AI request (full request and full response) to its own file under the airequests folder when enabled.",
        "default": "false",
        "type": "bool",
    },
}

READ_ONLY_SETTINGS = {
    "ai_efficiency_score": {
        "description": "AI-provided regex efficiency score (0-100) based on duplicate/similar pattern review.",
        "default": 0.0,
        "type": "float",
    }
}


def _get_ai_efficiency_score():
    raw = get_setting("ai_efficiency_score", "0.0")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _get_read_only_setting_value(key):
    if key == "ai_efficiency_score":
        return _get_ai_efficiency_score()

    raise ValueError(f"Unknown read-only setting: {key}")


def _normalize_setting_value(key, value):
    meta = EDITABLE_SETTINGS[key]

    if meta["type"] == "string":
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        if not meta.get("allow_empty", False) and not value.strip():
            raise ValueError("value must be a non-empty string")
        return value

    if meta["type"] == "int":
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("value must be an integer") from exc

        min_value = meta.get("min")
        if min_value is not None and parsed < min_value:
            raise ValueError(f"value must be >= {min_value}")

        return str(parsed)

    if meta["type"] == "float":
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("value must be a number") from exc

        min_value = meta.get("min")
        if min_value is not None and parsed < min_value:
            raise ValueError(f"value must be >= {min_value}")

        return str(parsed)

    if meta["type"] == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("true", "1", "yes", "on"):
                return "true"
            if normalized in ("false", "0", "no", "off"):
                return "false"

        raise ValueError("value must be a boolean")

    if meta["type"] == "json_list_of_pairs":
        # Accept either a JSON string or a native Python list
        if isinstance(value, list):
            pairs = value
        elif isinstance(value, str):
            try:
                pairs = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("value must be a valid JSON array") from exc
        else:
            raise ValueError("value must be a JSON array or a JSON string")

        if not isinstance(pairs, list):
            raise ValueError("value must be a JSON array")

        for i, entry in enumerate(pairs):
            if (
                not isinstance(entry, list)
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not isinstance(entry[1], str)
                or not entry[0]
                or not entry[1]
            ):
                raise ValueError(
                    f'entry {i} must be a two-element array of non-empty strings: ["regex_pattern", "TOKEN_NAME"]'
                )

        return json.dumps(pairs)

    raise ValueError("unsupported setting type")


def _typed_setting_value(key, raw_value):
    if raw_value is None:
        return None

    meta = EDITABLE_SETTINGS[key]
    if meta["type"] == "int":
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return raw_value

    if meta["type"] == "float":
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return raw_value

    if meta["type"] == "bool":
        return str(raw_value).strip().lower() == "true"

    if meta["type"] == "json_list_of_pairs":
        try:
            return json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return raw_value

    return raw_value


def setup_settings_routes(app):

    @app.route("/api/settings", method=["GET"])
    def api_get_settings():
        logger = logging.getLogger(__name__)
        try:
            result = []
            for key, meta in EDITABLE_SETTINGS.items():
                value = get_setting(key)
                default_value = _typed_setting_value(key, meta["default"])
                result.append(
                    {
                        "key": key,
                        "value": _typed_setting_value(key, value),
                        "default": default_value,
                        "is_custom": value is not None,
                        "description": meta["description"],
                        "type": meta["type"],
                        "read_only": False,
                    }
                )

            for key, meta in READ_ONLY_SETTINGS.items():
                result.append(
                    {
                        "key": key,
                        "value": _get_read_only_setting_value(key),
                        "default": meta["default"],
                        "is_custom": False,
                        "description": meta["description"],
                        "type": meta["type"],
                        "read_only": True,
                    }
                )

            response.content_type = "application/json"
            return json.dumps(result)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get settings: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings", method=["POST"])
    def api_create_setting():
        logger = logging.getLogger(__name__)
        try:
            body = request.json or {}
            key = body.get("key")
            if not key:
                response.status = 400
                return json.dumps({"error": "Missing required field: key"})

            if key in READ_ONLY_SETTINGS:
                response.status = 403
                return json.dumps({"error": f"Setting is read-only: {key}"})

            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})

            if get_setting(key) is not None:
                response.status = 409
                return json.dumps({"error": f"Setting already exists: {key}"})

            if "value" not in body:
                response.status = 400
                return json.dumps({"error": "Missing required field: value"})

            normalized_value = _normalize_setting_value(key, body["value"])
            set_setting(key, normalized_value)

            response.content_type = "application/json"
            response.status = 201
            return json.dumps(
                {
                    "status": "created",
                    "key": key,
                    "value": _typed_setting_value(key, normalized_value),
                }
            )
        except ValueError as e:
            response.status = 400
            return json.dumps({"error": str(e)})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to create setting: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>", method=["GET"])
    def api_get_setting(key):
        logger = logging.getLogger(__name__)
        try:
            if key in READ_ONLY_SETTINGS:
                meta = READ_ONLY_SETTINGS[key]
                response.content_type = "application/json"
                return json.dumps(
                    {
                        "key": key,
                        "value": _get_read_only_setting_value(key),
                        "default": meta["default"],
                        "is_custom": False,
                        "description": meta["description"],
                        "type": meta["type"],
                        "read_only": True,
                    }
                )

            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})

            meta = EDITABLE_SETTINGS[key]
            value = get_setting(key)
            response.content_type = "application/json"
            default_value = _typed_setting_value(key, meta["default"])
            return json.dumps(
                {
                    "key": key,
                    "value": _typed_setting_value(key, value),
                    "default": default_value,
                    "is_custom": value is not None,
                    "description": meta["description"],
                    "type": meta["type"],
                    "read_only": False,
                }
            )
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>", method=["PUT"])
    def api_set_setting(key):
        logger = logging.getLogger(__name__)
        try:
            if key in READ_ONLY_SETTINGS:
                response.status = 403
                return json.dumps({"error": f"Setting is read-only: {key}"})

            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})

            body = request.json or {}
            if "value" not in body:
                response.status = 400
                return json.dumps({"error": "Missing required field: value"})

            normalized_value = _normalize_setting_value(key, body["value"])
            set_setting(key, normalized_value)

            log_info(logger, f"[INFO] Setting '{key}' updated")
            response.content_type = "application/json"
            return json.dumps(
                {
                    "status": "ok",
                    "key": key,
                    "value": _typed_setting_value(key, normalized_value),
                }
            )
        except ValueError as e:
            response.status = 400
            return json.dumps({"error": str(e)})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to update setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>", method=["POST"])
    def api_create_setting_by_key(key):
        logger = logging.getLogger(__name__)
        try:
            if key in READ_ONLY_SETTINGS:
                response.status = 403
                return json.dumps({"error": f"Setting is read-only: {key}"})

            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})

            if get_setting(key) is not None:
                response.status = 409
                return json.dumps({"error": f"Setting already exists: {key}"})

            body = request.json or {}
            if "value" not in body:
                response.status = 400
                return json.dumps({"error": "Missing required field: value"})

            normalized_value = _normalize_setting_value(key, body["value"])
            set_setting(key, normalized_value)

            log_info(logger, f"[INFO] Setting '{key}' created")
            response.content_type = "application/json"
            response.status = 201
            return json.dumps(
                {
                    "status": "created",
                    "key": key,
                    "value": _typed_setting_value(key, normalized_value),
                }
            )
        except ValueError as e:
            response.status = 400
            return json.dumps({"error": str(e)})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to create setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>/reset", method=["POST"])
    def api_reset_setting(key):
        logger = logging.getLogger(__name__)
        try:
            if key in READ_ONLY_SETTINGS:
                response.status = 403
                return json.dumps({"error": f"Setting is read-only: {key}"})

            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})

            delete_setting(key)
            log_info(logger, f"[INFO] Setting '{key}' reset to default")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "key": key})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to reset setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/settings/<key>", method=["DELETE"])
    def api_delete_setting(key):
        logger = logging.getLogger(__name__)
        try:
            if key in READ_ONLY_SETTINGS:
                response.status = 403
                return json.dumps({"error": f"Setting is read-only: {key}"})

            if key not in EDITABLE_SETTINGS:
                response.status = 404
                return json.dumps({"error": f"Unknown setting: {key}"})

            if get_setting(key) is None:
                response.status = 404
                return json.dumps({"error": f"Setting not found: {key}"})

            delete_setting(key)
            log_info(logger, f"[INFO] Setting '{key}' deleted")
            response.content_type = "application/json"
            return json.dumps({"status": "deleted", "key": key})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to delete setting '{key}': {e}")
            response.status = 500
            return json.dumps({"error": str(e)})
