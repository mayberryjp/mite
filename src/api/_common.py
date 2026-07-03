"""Shared helpers for the Bottle API routes."""

import functools
import json
import logging

from bottle import response

from src.utils.locallogging import log_error


def json_endpoint(func):
    """Wrap a Bottle route handler with shared JSON response handling.

    - Sets the JSON content type for the response.
    - Maps an uncaught ValueError to HTTP 400 with ``{"error": ...}``.
    - Maps any other uncaught exception to HTTP 500 with ``{"error": ...}`` and
      logs it under the handler's own module logger.

    Handlers may still set ``response.status`` and return their own JSON
    payloads (e.g. 404 responses) directly; those are returned unchanged.
    """
    logger = logging.getLogger(func.__module__)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        response.content_type = "application/json"
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            response.status = 400
            return json.dumps({"error": str(e)})
        except Exception as e:
            log_error(logger, f"[ERROR] {func.__name__} failed: {e}")
            response.status = 500
            return json.dumps({"error": str(e)})

    return wrapper
