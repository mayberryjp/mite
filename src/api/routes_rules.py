import json
import logging

from bottle import Bottle, request, response

from src.core.db import get_all_patterns, get_pattern_by_id, update_pattern_user_override
from src.utils.locallogging import log_error, log_info

VALID_CLASSIFICATIONS = {"critical", "high", "medium", "low", "noise", None}


def setup_patterns_routes(app):

    @app.route("/api/patterns", method=["GET"])
    def api_get_patterns():
        logger = logging.getLogger(__name__)
        try:
            limit = int(request.params.get("limit", 100))
            offset = int(request.params.get("offset", 0))
            classification = request.params.get("classification")

            items, total = get_all_patterns(
                limit=limit, offset=offset, classification=classification,
            )

            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(items)} patterns (total {total})")
            return json.dumps({"items": items, "limit": limit, "offset": offset, "total": total})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get patterns: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/<pattern_id:int>", method=["GET"])
    def api_get_pattern(pattern_id):
        logger = logging.getLogger(__name__)
        try:
            pattern = get_pattern_by_id(pattern_id)
            if not pattern:
                response.status = 404
                return {"error": "Pattern not found"}

            response.content_type = "application/json"
            return json.dumps(pattern)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get pattern {pattern_id}: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/<pattern_id:int>", method=["PUT"])
    def api_update_pattern(pattern_id):
        logger = logging.getLogger(__name__)
        try:
            pattern = get_pattern_by_id(pattern_id)
            if not pattern:
                response.status = 404
                return {"error": "Pattern not found"}

            data = request.json or {}
            user_override = data.get("classification")

            if user_override is not None and user_override not in {"critical", "high", "medium", "low", "noise"}:
                response.status = 400
                return {"error": f"Invalid classification: {user_override}"}

            update_pattern_user_override(pattern_id, user_override)

            log_info(logger, f"[INFO] Pattern {pattern_id} override set to '{user_override}'")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "pattern_id": pattern_id, "user_override": user_override})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to update pattern {pattern_id}: {e}")
            response.status = 500
            return {"error": str(e)}
