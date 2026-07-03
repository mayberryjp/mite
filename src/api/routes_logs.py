import json
import logging

from bottle import request

from src.api._common import json_endpoint
from src.core.db import (
    delete_all_logs,
    delete_logs_for_noise_patterns,
    get_hourly_dropped_counts,
    get_hourly_log_counts,
    get_hourly_noise_counts,
    get_hourly_too_small_counts,
    get_logs,
    get_recent_logs,
)
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)


def setup_logs_routes(app):

    @app.route("/api/logs", method=["GET"])
    @json_endpoint
    def api_get_logs():
        limit = int(request.params.get("limit", 100))
        offset = int(request.params.get("offset", 0))
        host = request.params.get("host")
        source_ip = request.params.get("source_ip")
        program = request.params.get("program")
        severity = request.params.get("severity")
        search = request.params.get("search")
        start = request.params.get("start")
        end = request.params.get("end")

        items, total = get_logs(
            limit=limit,
            offset=offset,
            host=host,
            source_ip=source_ip,
            program=program,
            severity=severity,
            search=search,
            start=start,
            end=end,
        )

        log_info(logger, f"[INFO] Retrieved {len(items)} logs (total {total})")
        return json.dumps(
            {"items": items, "limit": limit, "offset": offset, "total": total}
        )

    @app.route("/api/logs/recent", method=["GET"])
    @json_endpoint
    def api_get_recent_logs():
        after_id = int(request.params.get("after_id", 0))
        limit = int(request.params.get("limit", 50))

        items = get_recent_logs(after_id=after_id, limit=limit)
        return json.dumps(items)

    @app.route("/api/logs", method=["DELETE"])
    @json_endpoint
    def api_delete_all_logs():
        deleted = delete_all_logs()
        log_info(logger, f"[INFO] Deleted all logs ({deleted} total)")
        return json.dumps({"status": "ok", "deleted": deleted})

    @app.route("/api/logs/hourly", method=["GET"])
    @json_endpoint
    def api_get_hourly_log_counts():
        hours = int(request.params.get("hours", 24))
        stats = get_hourly_log_counts(hours=hours)
        return json.dumps({"hours": hours, "stats": stats})

    @app.route("/api/logs/noise/hourly", method=["GET"])
    @json_endpoint
    def api_get_hourly_noise_counts():
        hours = int(request.params.get("hours", 24))
        stats = get_hourly_noise_counts(hours=hours)
        return json.dumps({"hours": hours, "stats": stats})

    @app.route("/api/logs/dropped/hourly", method=["GET"])
    @json_endpoint
    def api_get_hourly_dropped_counts():
        hours = int(request.params.get("hours", 24))
        stats = get_hourly_dropped_counts(hours=hours)
        return json.dumps({"hours": hours, "stats": stats})

    @app.route("/api/logs/too-small/hourly", method=["GET"])
    @json_endpoint
    def api_get_hourly_too_small_counts():
        hours = int(request.params.get("hours", 24))
        stats = get_hourly_too_small_counts(hours=hours)
        return json.dumps({"hours": hours, "stats": stats})

    @app.route("/api/logs/cleanup-noise", method=["POST"])
    @json_endpoint
    def api_cleanup_noise_logs():
        deleted = delete_logs_for_noise_patterns()
        log_info(logger, f"[INFO] Deleted {deleted} logs marked as noise")
        return json.dumps({"status": "ok", "deleted": deleted})
