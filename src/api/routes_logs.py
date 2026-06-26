import json
import logging

from bottle import request, response

from src.core.db import (
    delete_all_logs,
    delete_logs_for_noise_patterns,
    get_hourly_log_counts,
    get_hourly_noise_counts,
    get_logs,
    get_recent_logs,
)
from src.utils.locallogging import log_error, log_info


def setup_logs_routes(app):

    @app.route("/api/logs", method=["GET"])
    def api_get_logs():
        logger = logging.getLogger(__name__)
        try:
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

            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(items)} logs (total {total})")
            return json.dumps(
                {"items": items, "limit": limit, "offset": offset, "total": total}
            )
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get logs: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/logs/recent", method=["GET"])
    def api_get_recent_logs():
        logger = logging.getLogger(__name__)
        try:
            after_id = int(request.params.get("after_id", 0))
            limit = int(request.params.get("limit", 50))

            items = get_recent_logs(after_id=after_id, limit=limit)

            response.content_type = "application/json"
            return json.dumps(items)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get recent logs: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/logs", method=["DELETE"])
    def api_delete_all_logs():
        logger = logging.getLogger(__name__)
        try:
            deleted = delete_all_logs()
            log_info(logger, f"[INFO] Deleted all logs ({deleted} total)")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "deleted": deleted})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to delete all logs: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/logs/hourly", method=["GET"])
    def api_get_hourly_log_counts():
        logger = logging.getLogger(__name__)
        try:
            hours = int(request.params.get("hours", 24))
            stats = get_hourly_log_counts(hours=hours)
            response.content_type = "application/json"
            return json.dumps({"hours": hours, "stats": stats})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get hourly log counts: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/logs/noise/hourly", method=["GET"])
    def api_get_hourly_noise_counts():
        logger = logging.getLogger(__name__)
        try:
            hours = int(request.params.get("hours", 24))
            stats = get_hourly_noise_counts(hours=hours)
            response.content_type = "application/json"
            return json.dumps({"hours": hours, "stats": stats})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get hourly noise counts: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/logs/cleanup-noise", method=["POST"])
    def api_cleanup_noise_logs():
        logger = logging.getLogger(__name__)
        try:
            deleted = delete_logs_for_noise_patterns()
            log_info(logger, f"[INFO] Deleted {deleted} logs marked as noise")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "deleted": deleted})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to cleanup noise logs: {e}")
            response.status = 500
            return {"error": str(e)}
