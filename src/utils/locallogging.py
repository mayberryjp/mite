import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stdout,
)


def log_info(logger, message):
    logger.info(message)


def log_error(logger, message):
    logger.error(message)


def log_warn(logger, message):
    logger.warning(message)


def log_debug(logger, message):
    logger.debug(message)
