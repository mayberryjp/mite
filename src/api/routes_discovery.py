import json
import logging

from bottle import Bottle, request, response

from src.core.db import get_pending_patterns
from src.utils.locallogging import log_error, log_info


def setup_discovery_routes(app):

    @app.route("/api/ai/pending", method=["GET"])
    def api_get_pending():
        logger = logging.getLogger(__name__)
        try:
            limit = int(request.params.get("limit", 50))
            pending = get_pending_patterns(limit=limit)
            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(pending)} pending patterns")
            return json.dumps(pending)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get pending patterns: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/ai/classify", method=["POST"])
    def api_trigger_classification():
        logger = logging.getLogger(__name__)
        try:
            from src.core.config import AI_DISCOVERY_ENABLED
            if not AI_DISCOVERY_ENABLED:
                response.status = 400
                return {"error": "AI discovery is not enabled"}

            from src.core.ai_discovery import classify_patterns
            pending = get_pending_patterns(limit=20)
            if not pending:
                response.content_type = "application/json"
                return json.dumps({"status": "ok", "message": "No pending patterns to classify"})

            result = classify_patterns(pending)

            response.content_type = "application/json"
            log_info(logger, f"[INFO] AI classification triggered: {result}")
            return json.dumps(result)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to trigger AI classification: {e}")
            response.status = 500
            return {"error": str(e)}
