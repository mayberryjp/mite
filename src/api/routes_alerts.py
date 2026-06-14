import json
import logging

from bottle import Bottle, request, response

from src.core.db import get_alerts, get_hourly_alert_counts, delete_all_alerts
from src.utils.locallogging import log_error, log_info


def setup_alerts_routes(app):

    @app.route("/api/alerts", method=["GET"])
    def api_get_alerts():
        logger = logging.getLogger(__name__)
        try:
            limit = int(request.params.get("limit", 100))
            offset = int(request.params.get("offset", 0))
            severity = request.params.get("severity")
            host = request.params.get("host")
            source_ip = request.params.get("source_ip")
            pattern_id = request.params.get("pattern_id")
            search = request.params.get("search")

            items, total = get_alerts(
                limit=limit, offset=offset, severity=severity,
                host=host, source_ip=source_ip, pattern_id=pattern_id,
                search=search,
            )

            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(items)} alerts (total {total})")
            return json.dumps({"items": items, "limit": limit, "offset": offset, "total": total})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get alerts: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/alerts", method=["DELETE"])
    def api_delete_all_alerts():
        logger = logging.getLogger(__name__)
        try:
            deleted = delete_all_alerts()
            log_info(logger, f"[INFO] Deleted all alerts ({deleted} total)")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "deleted": deleted})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to delete alerts: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/alerts/hourly", method=["GET"])
    def api_get_hourly_alert_counts():
        logger = logging.getLogger(__name__)
        try:
            hours = int(request.params.get("hours", 24))
            stats = get_hourly_alert_counts(hours=hours)
            response.content_type = "application/json"
            return json.dumps({"hours": hours, "stats": stats})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get hourly alert counts: {e}")
            response.status = 500
            return {"error": str(e)}
