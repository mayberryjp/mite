import json
import logging
import re

from bottle import request, response

from src.core.db import (
    delete_all_patterns,
    delete_old_patterns,
    delete_pattern,
    get_all_pattern_stats,
    get_all_patterns,
    get_hourly_new_pattern_counts,
    get_logs_by_pattern,
    get_pattern_by_id,
    get_pattern_stats,
    move_low_patterns_to_noise,
    update_pattern_ai_explanation,
    update_pattern_regex,
    update_pattern_title,
    update_pattern_user_override,
)
from src.utils.locallogging import log_error, log_info

VALID_CLASSIFICATIONS = {"critical", "high", "medium", "low", "noise"}


def setup_patterns_routes(app):

    @app.route("/api/patterns", method=["GET"])
    def api_get_patterns():
        logger = logging.getLogger(__name__)
        try:
            limit_param = request.params.get("limit")
            limit = int(limit_param) if limit_param is not None else None
            offset = int(request.params.get("offset", 0))
            classification = request.params.get("classification")

            items, total = get_all_patterns(
                limit=limit,
                offset=offset,
                classification=classification,
            )

            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(items)} patterns (total {total})")
            return json.dumps(
                {"items": items, "limit": limit, "offset": offset, "total": total}
            )
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

            for field in ["classification", "match_regex", "title", "ai_explanation"]:
                if field in data and data[field] is None:
                    response.status = 400
                    return {"error": f"{field} cannot be null"}

            user_override = data.get("classification")
            match_regex = data.get("match_regex")
            title = data.get("title")
            ai_explanation = data.get("ai_explanation")

            if user_override is not None and user_override not in {
                "critical",
                "high",
                "medium",
                "low",
                "noise",
            }:
                response.status = 400
                return {"error": f"Invalid classification: {user_override}"}

            if title is not None:
                if title == "":
                    update_pattern_title(pattern_id, None)
                else:
                    update_pattern_title(pattern_id, title[:40])

            if ai_explanation is not None:
                if ai_explanation == "":
                    update_pattern_ai_explanation(pattern_id, None)
                else:
                    update_pattern_ai_explanation(pattern_id, ai_explanation)

            if match_regex is not None:
                if match_regex == "":
                    update_pattern_regex(pattern_id, None)
                else:
                    try:
                        re.compile(match_regex)
                    except re.error as e:
                        response.status = 400
                        return {"error": f"Invalid regex: {e}"}
                    update_pattern_regex(pattern_id, match_regex)

            if "classification" in data:
                update_pattern_user_override(pattern_id, user_override)

            log_info(logger, f"[INFO] Pattern {pattern_id} updated")
            response.content_type = "application/json"
            result = {"status": "ok", "pattern_id": pattern_id}
            if "classification" in data:
                result["user_override"] = user_override
            if match_regex is not None:
                result["match_regex"] = match_regex or None
            if title is not None:
                result["title"] = title[:40] if title else None
            if ai_explanation is not None:
                result["ai_explanation"] = ai_explanation or None
            return json.dumps(result)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to update pattern {pattern_id}: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/<pattern_id:int>", method=["DELETE"])
    def api_delete_pattern(pattern_id):
        logger = logging.getLogger(__name__)
        try:
            pattern = get_pattern_by_id(pattern_id)
            if not pattern:
                response.status = 404
                return {"error": "Pattern not found"}
            delete_pattern(pattern_id)
            log_info(logger, f"[INFO] Deleted pattern {pattern_id}")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "pattern_id": pattern_id})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to delete pattern {pattern_id}: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns", method=["DELETE"])
    def api_delete_all_patterns():
        logger = logging.getLogger(__name__)
        try:
            deleted = delete_all_patterns()
            log_info(logger, f"[INFO] Deleted all patterns ({deleted} total)")
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "deleted": deleted})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to delete all patterns: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/actions/low-to-noise", method=["POST"])
    def api_move_low_patterns_to_noise():
        logger = logging.getLogger(__name__)
        try:
            updated = move_low_patterns_to_noise()
            log_info(logger, f"[INFO] Reclassified {updated} low patterns to noise")
            response.content_type = "application/json"
            return json.dumps(
                {"status": "ok", "updated": updated, "from": "low", "to": "noise"}
            )
        except Exception as e:
            log_error(
                logger, f"[ERROR] Failed to reclassify low patterns to noise: {e}"
            )
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/actions/delete-old/<days:int>", method=["DELETE"])
    def api_delete_old_patterns(days):
        logger = logging.getLogger(__name__)
        try:
            if days < 1:
                response.status = 400
                return json.dumps({"error": "days must be >= 1"})
            deleted = delete_old_patterns(days)
            log_info(
                logger, f"[INFO] Deleted {deleted} patterns older than {days} days"
            )
            response.content_type = "application/json"
            return json.dumps({"status": "ok", "deleted": deleted, "days": days})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to delete old patterns: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    @app.route("/api/patterns/stats", method=["GET"])
    def api_get_all_pattern_stats():
        logger = logging.getLogger(__name__)
        try:
            hours = int(request.params.get("hours", 100))
            stats = get_all_pattern_stats(hours=hours)
            response.content_type = "application/json"
            return json.dumps(stats)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get pattern stats: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/hourly", method=["GET"])
    def api_get_hourly_new_pattern_counts():
        logger = logging.getLogger(__name__)
        try:
            hours = int(request.params.get("hours", 24))
            stats = get_hourly_new_pattern_counts(hours=hours)
            response.content_type = "application/json"
            return json.dumps({"hours": hours, "stats": stats})
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get hourly pattern counts: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/<pattern_id:int>/stats", method=["GET"])
    def api_get_pattern_stats(pattern_id):
        logger = logging.getLogger(__name__)
        try:
            hours = int(request.params.get("hours", 100))
            stats = get_pattern_stats(pattern_id, hours=hours)
            response.content_type = "application/json"
            return json.dumps(
                {"pattern_id": pattern_id, "hours": hours, "stats": stats}
            )
        except Exception as e:
            log_error(
                logger, f"[ERROR] Failed to get stats for pattern {pattern_id}: {e}"
            )
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/patterns/<pattern_id:int>/logs", method=["GET"])
    def api_get_pattern_logs(pattern_id):
        logger = logging.getLogger(__name__)
        try:
            limit = int(request.params.get("limit", 100))
            offset = int(request.params.get("offset", 0))
            items, total = get_logs_by_pattern(pattern_id, limit=limit, offset=offset)
            response.content_type = "application/json"
            return json.dumps(
                {"items": items, "limit": limit, "offset": offset, "total": total}
            )
        except Exception as e:
            log_error(
                logger, f"[ERROR] Failed to get logs for pattern {pattern_id}: {e}"
            )
            response.status = 500
            return {"error": str(e)}
