import json
import logging

from bottle import request, response

from src.core.models import DEFAULT_AI_PROMPT_TEMPLATE
from src.core.db import get_setting, set_setting, connect_to_db, disconnect_from_db, execute_with_retry
from src.utils.locallogging import log_error, log_info

EDITABLE_SETTINGS = {
	"ai_prompt_template": {
		"description": "Prompt template sent to the AI for log pattern classification. Use {patterns} as the placeholder for pattern data.",
		"default": DEFAULT_AI_PROMPT_TEMPLATE,
		"type": "string",
		"allow_empty": False,
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
	"ai_api_daily_rate_limit": {
		"description": "Maximum number of AI API classification calls allowed in a rolling 24-hour window.",
		"default": "500",
		"type": "int",
		"min": 1,
	},
}


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

	if meta["type"] == "bool":
		return str(raw_value).strip().lower() == "true"

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
				result.append({
					"key": key,
					"value": _typed_setting_value(key, value),
					"default": default_value,
					"is_custom": value is not None,
					"description": meta["description"],
					"type": meta["type"],
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
			default_value = _typed_setting_value(key, meta["default"])
			return json.dumps({
				"key": key,
				"value": _typed_setting_value(key, value),
				"default": default_value,
				"is_custom": value is not None,
				"description": meta["description"],
				"type": meta["type"],
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

			normalized_value = _normalize_setting_value(key, body["value"])
			set_setting(key, normalized_value)

			log_info(logger, f"[INFO] Setting '{key}' updated")
			response.content_type = "application/json"
			return json.dumps({
				"status": "ok",
				"key": key,
				"value": _typed_setting_value(key, normalized_value),
			})
		except ValueError as e:
			response.status = 400
			return json.dumps({"error": str(e)})
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
