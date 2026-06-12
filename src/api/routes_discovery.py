import json
import logging
import os

from bottle import Bottle, request, response

from src.core.config import MITE_ANALYSIS_DIR
from src.core.db import get_ai_analyses, get_ai_analysis_by_id
from src.utils.locallogging import log_error, log_info


def setup_discovery_routes(app):

    @app.route("/api/ai/analyses", method=["GET"])
    def api_get_analyses():
        logger = logging.getLogger(__name__)
        try:
            analyses = get_ai_analyses()
            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(analyses)} AI analyses")
            return json.dumps(analyses)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get AI analyses: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/ai/analyses/<analysis_id:int>", method=["GET"])
    def api_get_analysis_detail(analysis_id):
        logger = logging.getLogger(__name__)
        try:
            analysis = get_ai_analysis_by_id(analysis_id)
            if not analysis:
                response.status = 404
                return {"error": "Analysis not found"}

            markdown_content = ""
            md_path = analysis.get("markdown_path", "")
            if md_path and os.path.exists(md_path):
                with open(md_path, "r", encoding="utf-8") as f:
                    markdown_content = f.read()

            analysis["markdown_content"] = markdown_content
            response.content_type = "application/json"
            return json.dumps(analysis)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get AI analysis {analysis_id}: {e}")
            response.status = 500
            return {"error": str(e)}

    @app.route("/api/ai/analyze", method=["POST"])
    def api_trigger_analysis():
        logger = logging.getLogger(__name__)
        try:
            from src.core.config import AI_DISCOVERY_ENABLED
            if not AI_DISCOVERY_ENABLED:
                response.status = 400
                return {"error": "AI discovery is not enabled"}

            data = request.json or {}
            host = data.get("host")
            source_ip = data.get("source_ip")
            sample_count = data.get("sample_count", 100)

            from src.core.ai_discovery import run_ai_analysis
            result = run_ai_analysis(source_ip=source_ip, host=host, sample_count=sample_count)

            response.content_type = "application/json"
            log_info(logger, f"[INFO] AI analysis triggered for source_ip={source_ip} host={host}")
            return json.dumps(result)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to trigger AI analysis: {e}")
            response.status = 500
            return {"error": str(e)}
