"""Consolidated settings loader for worker processes.

Eliminates duplication of _get_int_setting() and _get_float_setting()
across multiple worker modules (processor, udp_listener, tcp_listener, ai_worker).

These functions load configurable settings from the database with validation.
"""

import logging

from src.core.db import get_setting
from src.utils.locallogging import log_error


def get_int_setting(key, default_value, min_value=1):
    """Load an integer setting from the database with validation.
    
    Args:
        key: Setting key in the database.
        default_value: Default value if key not found or invalid.
        min_value: Minimum acceptable value (default 1).
        
    Returns:
        Parsed integer value, or default_value if invalid.
    """
    raw_value = get_setting(key, str(default_value))
    try:
        parsed = int(raw_value)
        if parsed < min_value:
            raise ValueError(f"{key} must be >= {min_value}")
        return parsed
    except (TypeError, ValueError):
        log_error(
            logging.getLogger(__name__),
            f"[ERROR] Invalid setting '{key}' value '{raw_value}', using default {default_value}",
        )
        return default_value


def get_float_setting(key, default_value, min_value=0.1):
    """Load a float setting from the database with validation.
    
    Args:
        key: Setting key in the database.
        default_value: Default value if key not found or invalid.
        min_value: Minimum acceptable value (default 0.1).
        
    Returns:
        Parsed float value, or default_value if invalid.
    """
    raw_value = get_setting(key, str(default_value))
    try:
        parsed = float(raw_value)
        if parsed < min_value:
            raise ValueError(f"{key} must be >= {min_value}")
        return parsed
    except (TypeError, ValueError):
        log_error(
            logging.getLogger(__name__),
            f"[ERROR] Invalid setting '{key}' value '{raw_value}', using default {default_value}",
        )
        return default_value
