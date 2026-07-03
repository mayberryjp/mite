import json
import logging
import os
import re
from datetime import datetime

from bottle import request, response

from src.api._common import json_endpoint
from src.core.config import MITE_DB_PATH, VERSION
from src.core.db import (
    delete_all_patterns,
    delete_old_patterns,
    delete_pattern,
    get_all_pattern_stats,
    get_all_patterns,
    get_hit_count_sum_by_classification,
    get_hourly_new_pattern_counts,
    get_logs_by_pattern,
    get_pattern_by_id,
    get_pattern_stats,
    move_low_patterns_to_noise,
    reset_all_pattern_hit_counts,
    update_pattern_ai_explanation,
    update_pattern_filter_at_listener,
    update_pattern_regex,
    update_pattern_title,
    update_pattern_user_override,
)
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)

VALID_CLASSIFICATIONS = {"critical", "high", "medium", "low", "noise"}


def export_patterns_to_file(data_dir=None):
    """Dump all patterns to a timestamped JSON file in the data folder.

    Writes patterns_YYYYMMDDHHMM.json containing a structured, re-importable
    payload (export_version, exported_at, mite_version, count, patterns).
    Counter fields (hit_count) are written as 0 so imported patterns start
    fresh; the live database is not modified. Returns a dict with the
    filename, absolute path, and pattern count.
    """
    if data_dir is None:
        data_dir = os.path.dirname(MITE_DB_PATH) or "."
    os.makedirs(data_dir, exist_ok=True)

    patterns, _ = get_all_patterns(limit=None)

    # Export counters as 0 so imported patterns start fresh (does not touch the DB).
    for pattern in patterns:
        pattern["hit_count"] = 0

    now = datetime.now()
    filename = f"patterns_{now.strftime('%Y%m%d%H%M')}.json"
    file_path = os.path.join(data_dir, filename)

    payload = {
        "export_version": 1,
        "exported_at": now.isoformat(timespec="seconds"),
        "mite_version": VERSION,
        "count": len(patterns),
        "patterns": patterns,
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return {"filename": filename, "path": file_path, "count": len(patterns)}


def setup_patterns_routes(app):

    @app.route("/api/patterns", method=["GET"])
    @json_endpoint
    def api_get_patterns():
        limit_param = request.params.get("limit")
        limit = int(limit_param) if limit_param is not None else None
        offset = int(request.params.get("offset", 0))
        classification = request.params.get("classification")

        items, total = get_all_patterns(
            limit=limit,
            offset=offset,
            classification=classification,
        )

        log_info(logger, f"[INFO] Retrieved {len(items)} patterns (total {total})")
        return json.dumps(
            {"items": items, "limit": limit, "offset": offset, "total": total}
        )

    @app.route("/api/patterns/match", method=["POST"])
    @json_endpoint
    def api_match_pattern():
        """Run a raw log message through the processor regex list.

        Accepts JSON {"log": "..."} (aliases: "message", "raw_message"), applies
        the same tokenization the processor uses, matches it against the stored
        pattern regexes, and reports which pattern id/name it matched.
        """
        from src.core.ai_discovery import preprocess_sample_for_ai
        from src.core.pattern_extractor import extract_pattern, hash_pattern
        from src.workers.processor import match_by_regex

        body = request.json or {}
        message = body.get("log") or body.get("message") or body.get("raw_message")
        if not isinstance(message, str) or not message.strip():
            response.status = 400
            return json.dumps({"error": "log must be a non-empty string"})

        # Mirror the processor: tokenize, then run against the regex list.
        tokenized_message = preprocess_sample_for_ai(message)
        normalized_pattern = extract_pattern(tokenized_message)

        pattern_id, effective_classification = match_by_regex(tokenized_message)

        result = {
            "matched": pattern_id is not None,
            "pattern_id": pattern_id,
            "name": None,
            "effective_classification": effective_classification,
            "tokenized_message": tokenized_message,
            "normalized_pattern": normalized_pattern,
            "pattern_hash": hash_pattern(normalized_pattern),
        }

        if pattern_id is not None:
            pattern = get_pattern_by_id(pattern_id)
            if pattern:
                result["name"] = pattern.get("title") or f"pattern_{pattern_id}"

        log_info(
            logger,
            f"[INFO] Pattern match test -> matched={result['matched']} "
            f"pattern_id={pattern_id}",
        )
        return json.dumps(result)

    @app.route("/api/patterns/<pattern_id:int>", method=["GET"])
    @json_endpoint
    def api_get_pattern(pattern_id):
        pattern = get_pattern_by_id(pattern_id)
        if not pattern:
            response.status = 404
            return {"error": "Pattern not found"}

        return json.dumps(pattern)

    @app.route("/api/patterns/<pattern_id:int>", method=["PUT"])
    @json_endpoint
    def api_update_pattern(pattern_id):
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
        filter_at_listener = data.get("filter_at_listener")

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

        if filter_at_listener is not None:
            update_pattern_filter_at_listener(pattern_id, filter_at_listener)
            log_info(
                logger,
                f"[INFO] Pattern {pattern_id} filter_at_listener set to {filter_at_listener}",
            )

        log_info(logger, f"[INFO] Pattern {pattern_id} updated")
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

    @app.route("/api/patterns/<pattern_id:int>", method=["DELETE"])
    @json_endpoint
    def api_delete_pattern(pattern_id):
        pattern = get_pattern_by_id(pattern_id)
        if not pattern:
            response.status = 404
            return {"error": "Pattern not found"}
        delete_pattern(pattern_id)
        log_info(logger, f"[INFO] Deleted pattern {pattern_id}")
        return json.dumps({"status": "ok", "pattern_id": pattern_id})

    @app.route("/api/patterns", method=["DELETE"])
    @json_endpoint
    def api_delete_all_patterns():
        deleted = delete_all_patterns()
        log_info(logger, f"[INFO] Deleted all patterns ({deleted} total)")
        return json.dumps({"status": "ok", "deleted": deleted})

    @app.route("/api/patterns/export", method=["POST"])
    @json_endpoint
    def api_export_patterns():
        result = export_patterns_to_file()
        log_info(
            logger,
            f"[INFO] Exported {result['count']} patterns to {result['path']}",
        )
        return json.dumps({"status": "ok", **result})

    @app.route("/api/patterns/actions/reset-hit-counts", method=["POST"])
    @json_endpoint
    def api_reset_all_pattern_hit_counts():
        updated = reset_all_pattern_hit_counts()
        log_info(logger, f"[INFO] Reset hit counts for {updated} patterns")
        return json.dumps({"status": "ok", "updated": updated})

    @app.route("/api/patterns/hits-by-classification", method=["GET"])
    @json_endpoint
    def api_get_hits_by_classification():
        items = get_hit_count_sum_by_classification()
        log_info(
            logger,
            f"[INFO] Retrieved hit count sums for {len(items)} classifications",
        )
        return json.dumps({"items": items})

    @app.route("/api/patterns/actions/low-to-noise", method=["POST"])
    @json_endpoint
    def api_move_low_patterns_to_noise():
        updated = move_low_patterns_to_noise()
        log_info(logger, f"[INFO] Reclassified {updated} low patterns to noise")
        return json.dumps(
            {"status": "ok", "updated": updated, "from": "low", "to": "noise"}
        )

    @app.route("/api/patterns/actions/delete-old/<days:int>", method=["DELETE"])
    @json_endpoint
    def api_delete_old_patterns(days):
        if days < 1:
            response.status = 400
            return json.dumps({"error": "days must be >= 1"})
        deleted = delete_old_patterns(days)
        log_info(logger, f"[INFO] Deleted {deleted} patterns older than {days} days")
        return json.dumps({"status": "ok", "deleted": deleted, "days": days})

    @app.route("/api/patterns/stats", method=["GET"])
    @json_endpoint
    def api_get_all_pattern_stats():
        hours = int(request.params.get("hours", 100))
        stats = get_all_pattern_stats(hours=hours)
        return json.dumps(stats)

    @app.route("/api/patterns/hourly", method=["GET"])
    @json_endpoint
    def api_get_hourly_new_pattern_counts():
        hours = int(request.params.get("hours", 24))
        stats = get_hourly_new_pattern_counts(hours=hours)
        return json.dumps({"hours": hours, "stats": stats})

    @app.route("/api/patterns/<pattern_id:int>/stats", method=["GET"])
    @json_endpoint
    def api_get_pattern_stats(pattern_id):
        hours = int(request.params.get("hours", 100))
        stats = get_pattern_stats(pattern_id, hours=hours)
        return json.dumps({"pattern_id": pattern_id, "hours": hours, "stats": stats})

    @app.route("/api/patterns/<pattern_id:int>/logs", method=["GET"])
    @json_endpoint
    def api_get_pattern_logs(pattern_id):
        limit = int(request.params.get("limit", 100))
        offset = int(request.params.get("offset", 0))
        items, total = get_logs_by_pattern(pattern_id, limit=limit, offset=offset)
        return json.dumps(
            {"items": items, "limit": limit, "offset": offset, "total": total}
        )
