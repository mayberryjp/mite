import json
import logging
import time

from bottle import Bottle, request, response

from src.api.routes_actions import setup_actions_routes
from src.api.routes_alerts import setup_alerts_routes
from src.api.routes_discovery import setup_discovery_routes
from src.api.routes_logs import setup_logs_routes
from src.api.routes_rules import setup_patterns_routes
from src.api.routes_settings import setup_settings_routes
from src.core.config import VERSION
from src.core.db import get_stats
from src.core.discord import is_discord_configured, send_discord_message
from src.utils.locallogging import log_error, log_info

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}

app = Bottle()
logger = logging.getLogger(__name__)

# Register all route groups
setup_logs_routes(app)
setup_actions_routes(app)
setup_alerts_routes(app)
setup_patterns_routes(app)
setup_discovery_routes(app)
setup_settings_routes(app)


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
            return json.dumps(
                {"status": "error", "message": "Failed to send test message"}
            )
    except Exception as e:
        log_error(logger, f"[ERROR] Failed to send test Discord message: {e}")
        response.status = 500
        return {"error": str(e)}


@app.hook("before_request")
def record_request_start_time():
    request.environ["mite_request_start"] = time.perf_counter()


@app.hook("after_request")
def log_request():
    start = request.environ.get("mite_request_start")
    elapsed_ms = ((time.perf_counter() - start) * 1000.0) if start else -1.0
    client_ip = request.environ.get("REMOTE_ADDR", "unknown")
    log_info(
        logger,
        f"[INFO] {request.method} {request.path} -> {response.status_code} ({elapsed_ms:.1f} ms) ip={client_ip}",
    )


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
    logger = logging.getLogger(__name__)
    log_info(logger, "[INFO] Waiting 5 seconds for database initialization...")
    time.sleep(5)
    log_info(logger, "[INFO] Starting Mite API server...")

    if is_discord_configured():
        send_discord_message(
            f"Mite startup complete. API server is starting. Version: {VERSION}"
        )

    from waitress import serve

    from src.core.config import MITE_API_HOST, MITE_API_PORT

    serve(app, host=MITE_API_HOST, port=MITE_API_PORT, threads=10)
