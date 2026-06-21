import json
import logging

from bottle import request, response

from src.core.db import (
    create_action,
    delete_action,
    get_action_by_id,
    get_actions,
    update_action,
)
from src.utils.locallogging import log_error, log_info


def _parse_bool(value, field_name):
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False

    raise ValueError(f"{field_name} must be a boolean")


def setup_actions_routes(app):

    @app.route("/api/actions", method=["GET"])
    def api_get_actions():
        logger = logging.getLogger(__name__)
        try:
            limit = int(request.params.get("limit", 100))
            offset = int(request.params.get("offset", 0))
            search = request.params.get("search")
            acknowledged_param = request.params.get("acknowledged")
            acknowledged = None
            if acknowledged_param is not None:
                acknowledged = _parse_bool(acknowledged_param, "acknowledged")

            items, total = get_actions(
                limit=limit,
                offset=offset,
                acknowledged=acknowledged,
                search=search,
            )

            response.content_type = "application/json"
            return json.dumps(
                {
                    "items": items,
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                }
            )
        except ValueError as e:
            response.status = 400
            return json.dumps({"error": str(e)})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get actions: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/actions/<action_id:int>", method=["GET"])
    def api_get_action(action_id):
        logger = logging.getLogger(__name__)
        try:
            item = get_action_by_id(action_id)
            if not item:
                response.status = 404
                return json.dumps({"error": "Action not found"})

            response.content_type = "application/json"
            return json.dumps(item)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get action {action_id}: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/actions", method=["POST"])
    def api_create_action():
        logger = logging.getLogger(__name__)
        try:
            body = request.json or {}
            action_text = body.get("action_text")
            if not isinstance(action_text, str) or not action_text.strip():
                response.status = 400
                return json.dumps({"error": "action_text must be a non-empty string"})

            acknowledged = False
            if "acknowledged" in body:
                acknowledged = _parse_bool(body.get("acknowledged"), "acknowledged")

            action_id = create_action(action_text.strip(), acknowledged)
            item = get_action_by_id(action_id)

            response.content_type = "application/json"
            response.status = 201
            log_info(logger, f"[INFO] Created action {action_id}")
            return json.dumps(item)
        except ValueError as e:
            response.status = 400
            return json.dumps({"error": str(e)})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to create action: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/actions/<action_id:int>", method=["PUT"])
    def api_update_action(action_id):
        logger = logging.getLogger(__name__)
        try:
            body = request.json or {}
            action_text = body.get("action_text") if "action_text" in body else None
            acknowledged = (
                _parse_bool(body.get("acknowledged"), "acknowledged")
                if "acknowledged" in body
                else None
            )

            if action_text is None and acknowledged is None:
                response.status = 400
                return json.dumps(
                    {"error": "At least one of action_text or acknowledged is required"}
                )

            if action_text is not None:
                if not isinstance(action_text, str) or not action_text.strip():
                    response.status = 400
                    return json.dumps(
                        {"error": "action_text must be a non-empty string"}
                    )
                action_text = action_text.strip()

            updated = update_action(
                action_id, action_text=action_text, acknowledged=acknowledged
            )
            if not updated:
                if get_action_by_id(action_id) is None:
                    response.status = 404
                    return json.dumps({"error": "Action not found"})
                response.status = 400
                return json.dumps({"error": "No valid fields provided"})

            item = get_action_by_id(action_id)
            response.content_type = "application/json"
            log_info(logger, f"[INFO] Updated action {action_id}")
            return json.dumps(item)
        except ValueError as e:
            response.status = 400
            return json.dumps({"error": str(e)})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to update action {action_id}: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/actions/<action_id:int>", method=["DELETE"])
    def api_delete_action(action_id):
        logger = logging.getLogger(__name__)
        try:
            deleted = delete_action(action_id)
            if not deleted:
                response.status = 404
                return json.dumps({"error": "Action not found"})

            response.content_type = "application/json"
            log_info(logger, f"[INFO] Deleted action {action_id}")
            return json.dumps({"status": "ok", "action_id": action_id})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to delete action {action_id}: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})
