import json
import logging

from bottle import request, response

from src.api._common import json_endpoint
from src.core.db import (
    delete_alert,
    delete_all_alerts,
    get_alerts,
    get_hourly_alert_counts,
)
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)


def setup_alerts_routes(app):

    @app.route("/api/alerts", method=["GET"])
    @json_endpoint
    def api_get_alerts():
        limit = int(request.params.get("limit", 100))
        offset = int(request.params.get("offset", 0))
        severity = request.params.get("severity")
        host = request.params.get("host")
        source_ip = request.params.get("source_ip")
        pattern_id = request.params.get("pattern_id")
        search = request.params.get("search")

        items, total = get_alerts(
            limit=limit,
            offset=offset,
            severity=severity,
            host=host,
            source_ip=source_ip,
            pattern_id=pattern_id,
            search=search,
        )

        log_info(logger, f"[INFO] Retrieved {len(items)} alerts (total {total})")
        return json.dumps(
            {"items": items, "limit": limit, "offset": offset, "total": total}
        )

    @app.route("/api/alerts", method=["DELETE"])
    @json_endpoint
    def api_delete_all_alerts():
        deleted = delete_all_alerts()
        log_info(logger, f"[INFO] Deleted all alerts ({deleted} total)")
        return json.dumps({"status": "ok", "deleted": deleted})

    @app.route("/api/alerts/<alert_id:int>", method=["DELETE"])
    @json_endpoint
    def api_delete_alert(alert_id):
        deleted = delete_alert(alert_id)
        if not deleted:
            response.status = 404
            return {"error": "Alert not found"}
        log_info(logger, f"[INFO] Deleted alert {alert_id}")
        return json.dumps({"status": "ok", "alert_id": alert_id})

    @app.route("/api/alerts/hourly", method=["GET"])
    @json_endpoint
    def api_get_hourly_alert_counts():
        hours = int(request.params.get("hours", 24))
        stats = get_hourly_alert_counts(hours=hours)
        return json.dumps({"hours": hours, "stats": stats})
