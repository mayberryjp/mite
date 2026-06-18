import os
import sys
import traceback
from datetime import datetime


def log_info(logger, message):
    """Log a message and print it to the console with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    script_name = os.path.basename(sys.argv[0])
    formatted_message = f"[{timestamp}] {script_name} {message}"
    print(formatted_message)
    logger.info(formatted_message)


def log_error(logger, message):
    """
    Log an error message and optionally report it to the cloud API, excluding specified messages.
    Also writes to the daily log file if enabled.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    script_name = os.path.basename(sys.argv[0])
    tb = traceback.extract_tb(sys.exc_info()[2])
    if tb:
        last_frame = tb[-1]
        file_name = os.path.basename(last_frame.filename)
        line_number = last_frame.lineno
    else:
        file_name = script_name
        line_number = "N/A"
    formatted_message = (
        f"[{timestamp}] {script_name}[/{file_name}/{line_number}] {message}"
    )
    print(formatted_message)
    logger.error(formatted_message)


def log_warn(logger, message):
    """Log a warning message and print it to the console with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    script_name = os.path.basename(sys.argv[0])
    formatted_message = f"[{timestamp}] {script_name} {message}"
    print(formatted_message)
    logger.warning(formatted_message)
