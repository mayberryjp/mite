import json
import logging

from bottle import request, response

from src.api._common import json_endpoint
from src.core.db import (
    acknowledge_all_actions,
    create_action,
    delete_action,
    get_action_by_id,
    get_actions,
    update_action,
)
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)


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

    @app.route("/api/actions/acknowledge-all", method=["POST"])
    @app.route("/api/actions/acknowledge_all", method=["POST"])
    @app.route("/actions/acknowledge-all", method=["POST"])
    @app.route("/actions/acknowledge_all", method=["POST"])
    @json_endpoint
    def api_acknowledge_all_actions():
        updated = acknowledge_all_actions()
        log_info(logger, f"[INFO] Acknowledged all actions ({updated} updated)")
        return json.dumps(
            {
                "status": "ok",
                "acknowledged": True,
                "updated": updated,
            }
        )

    @app.route("/api/actions", method=["GET"])
    @json_endpoint
    def api_get_actions():
        limit_param = request.params.get("limit")
        limit = int(limit_param) if limit_param is not None else None
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

        return json.dumps(
            {
                "items": items,
                "limit": limit,
                "offset": offset,
                "total": total,
            }
        )

    @app.route("/api/actions/<action_id:int>", method=["GET"])
    @json_endpoint
    def api_get_action(action_id):
        item = get_action_by_id(action_id)
        if not item:
            response.status = 404
            return json.dumps({"error": "Action not found"})

        return json.dumps(item)

    @app.route("/api/actions", method=["POST"])
    @json_endpoint
    def api_create_action():
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

        response.status = 201
        log_info(logger, f"[INFO] Created action {action_id}")
        return json.dumps(item)

    @app.route("/api/actions/<action_id:int>", method=["PUT"])
    @json_endpoint
    def api_update_action(action_id):
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
                return json.dumps({"error": "action_text must be a non-empty string"})
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
        log_info(logger, f"[INFO] Updated action {action_id}")
        return json.dumps(item)

    @app.route("/api/actions/<action_id:int>", method=["DELETE"])
    @json_endpoint
    def api_delete_action(action_id):
        deleted = delete_action(action_id)
        if not deleted:
            response.status = 404
            return json.dumps({"error": "Action not found"})

        log_info(logger, f"[INFO] Deleted action {action_id}")
        return json.dumps({"status": "ok", "action_id": action_id})
