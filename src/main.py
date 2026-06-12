import logging

from src.core.config import ensure_directories
from src.core.db import init_database
from src.utils.locallogging import log_info

logger = logging.getLogger(__name__)


def main():
    log_info(logger, "[INFO] Mite backend initializing...")
    ensure_directories()
    init_database()
    log_info(logger, "[INFO] Mite backend initialization complete")


if __name__ == "__main__":
    main()
