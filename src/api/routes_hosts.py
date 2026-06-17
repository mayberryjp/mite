import json
import logging

from bottle import response

from src.core.db import get_all_hosts
from src.utils.locallogging import log_error, log_info


def setup_hosts_routes(app):

    @app.route("/api/hosts", method=["GET"])
    def api_get_hosts():
        logger = logging.getLogger(__name__)
        try:
            hosts = get_all_hosts()

            response.content_type = "application/json"
            log_info(logger, f"[INFO] Retrieved {len(hosts)} hosts")
            return json.dumps(hosts)
        except Exception as e:
            log_error(logger, f"[ERROR] Failed to get hosts: {e}")
            response.status = 500
            return {"error": str(e)}
