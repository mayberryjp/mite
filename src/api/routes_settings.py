import json
import logging
import os

from bottle import request, response

from src.api._common import json_endpoint
from src.core.config import MITE_DB_PATH, MITE_LOGS_DB_PATH
from src.core.db import (
    delete_setting,
    get_discarded_too_small_count,
    get_setting,
    get_silently_dropped_count,
    set_setting,
)
from src.core.settings_schema import EDITABLE_SETTINGS, READ_ONLY_SETTINGS
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)


def _get_ai_efficiency_score():
    raw = get_setting("ai_efficiency_score", "0.0")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _get_file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _get_read_only_setting_value(key):
    if key == "ai_efficiency_score":
        return _get_ai_efficiency_score()

    if key == "silently_dropped_count":
        return get_silently_dropped_count()

    if key == "discarded_too_small_count":
        return get_discarded_too_small_count()

    if key == "mite_db_size_bytes":
        return _get_file_size(MITE_DB_PATH)

    if key == "logs_db_size_bytes":
        return _get_file_size(MITE_LOGS_DB_PATH)

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

    if meta["type"] == "syslog_classification":
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        normalized = value.strip().lower()
        valid_classifications = ["noise", "low", "medium", "high", "critical"]
        if normalized not in valid_classifications:
            raise ValueError(
                f"value must be one of: {', '.join(valid_classifications)}"
            )
        return normalized

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

    if meta["type"] == "syslog_classification":
        return str(raw_value).strip().lower()

    return raw_value


def setup_settings_routes(app):

    @app.route("/api/settings", method=["GET"])
    @json_endpoint
    def api_get_settings():
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

        return json.dumps(result)

    @app.route("/api/settings", method=["POST"])
    @json_endpoint
    def api_create_setting():
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

        response.status = 201
        return json.dumps(
            {
                "status": "created",
                "key": key,
                "value": _typed_setting_value(key, normalized_value),
            }
        )

    @app.route("/api/settings/<key>", method=["GET"])
    @json_endpoint
    def api_get_setting(key):
        if key in READ_ONLY_SETTINGS:
            meta = READ_ONLY_SETTINGS[key]
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

    @app.route("/api/settings/<key>", method=["PUT"])
    @json_endpoint
    def api_set_setting(key):
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
        return json.dumps(
            {
                "status": "ok",
                "key": key,
                "value": _typed_setting_value(key, normalized_value),
            }
        )

    @app.route("/api/settings/<key>", method=["POST"])
    @json_endpoint
    def api_create_setting_by_key(key):
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
        response.status = 201
        return json.dumps(
            {
                "status": "created",
                "key": key,
                "value": _typed_setting_value(key, normalized_value),
            }
        )

    @app.route("/api/settings/<key>/reset", method=["POST"])
    @json_endpoint
    def api_reset_setting(key):
        if key in READ_ONLY_SETTINGS:
            response.status = 403
            return json.dumps({"error": f"Setting is read-only: {key}"})

        if key not in EDITABLE_SETTINGS:
            response.status = 404
            return json.dumps({"error": f"Unknown setting: {key}"})

        delete_setting(key)
        log_info(logger, f"[INFO] Setting '{key}' reset to default")
        return json.dumps({"status": "ok", "key": key})

    @app.route("/api/settings/<key>", method=["DELETE"])
    @json_endpoint
    def api_delete_setting(key):
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
        return json.dumps({"status": "deleted", "key": key})
