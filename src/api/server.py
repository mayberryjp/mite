import json
import logging
import os

from bottle import Bottle, response

from src.core.config import VERSION, MITE_DB_PATH, AI_DISCOVERY_ENABLED
from src.core.db import get_stats
from src.core.discord import send_discord_message
from src.api.routes_logs import setup_logs_routes
from src.api.routes_alerts import setup_alerts_routes
from src.api.routes_hosts import setup_hosts_routes
from src.api.routes_rules import setup_rules_routes
from src.api.routes_discovery import setup_discovery_routes
from src.utils.locallogging import log_error, log_info

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}

app = Bottle()

# Register all route groups
setup_logs_routes(app)
setup_alerts_routes(app)
setup_hosts_routes(app)
setup_rules_routes(app)
setup_discovery_routes(app)


@app.route("/api/health", method=["GET"])
def api_health():
    response.content_type = "application/json"
    return json.dumps({"status": "ok", "version": VERSION})


@app.route("/api/stats", method=["GET"])
def api_stats():
    logger = logging.getLogger(__name__)
    try:
        stats = get_stats()
        response.content_type = "application/json"
        return json.dumps(stats)
    except Exception as e:
        log_error(logger, f"[ERROR] Failed to get stats: {e}")
        response.status = 500
        return {"error": str(e)}


@app.route("/api/discord/test", method=["POST"])
def api_test_discord():
    logger = logging.getLogger(__name__)
    try:
        success = send_discord_message(
            "🔔 Mite Test Alert\n\nThis is a test message from Mite."
        )
        response.content_type = "application/json"
        if success:
            return json.dumps({"status": "ok", "message": "Test message sent"})
        else:
            response.status = 500
            return json.dumps({"status": "error", "message": "Failed to send test message"})
    except Exception as e:
        log_error(logger, f"[ERROR] Failed to send test Discord message: {e}")
        response.status = 500
        return {"error": str(e)}


# CORS support
@app.hook("after_request")
def enable_cors():
    for key, value in CORS_HEADERS.items():
        response.headers[key] = value


@app.route("/<path:path>", method="OPTIONS")
@app.route("/", method="OPTIONS")
def options_handler(path=None):
    for key, value in CORS_HEADERS.items():
        response.headers[key] = value
    return {}


if __name__ == "__main__":
    import time

    logger = logging.getLogger(__name__)
    log_info(logger, "[INFO] Waiting 5 seconds for database initialization...")
    time.sleep(5)
    log_info(logger, "[INFO] Starting Mite API server...")

    from src.core.config import MITE_API_HOST, MITE_API_PORT
    from waitress import serve

    serve(app, host=MITE_API_HOST, port=MITE_API_PORT, threads=10)
