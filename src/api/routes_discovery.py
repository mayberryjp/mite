import json
import logging

from bottle import request

from src.api._common import json_endpoint
from src.core.db import get_pending_patterns
from src.core.settings_loader import get_int_setting
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)

AI_BATCH_SIZE_DEFAULT = 20


def setup_discovery_routes(app):

    @app.route("/api/ai/pending", method=["GET"])
    @json_endpoint
    def api_get_pending():
        limit = int(request.params.get("limit", 50))
        pending = get_pending_patterns(limit=limit)
        log_info(logger, f"[INFO] Retrieved {len(pending)} pending patterns")
        return json.dumps(pending)

    @app.route("/api/ai/classify", method=["POST"])
    @json_endpoint
    def api_trigger_classification():
        from src.core.ai_discovery import classify_patterns

        batch_size = get_int_setting("ai_batch_size", AI_BATCH_SIZE_DEFAULT)
        pending = get_pending_patterns(limit=batch_size)
        if not pending:
            return json.dumps(
                {"status": "ok", "message": "No pending patterns to classify"}
            )

        result = classify_patterns(pending)
        log_info(logger, f"[INFO] AI classification triggered: {result}")
        return json.dumps(result)
