"""
utils/logger.py — Loguru setup.

Features:
  - Console: coloured, human-readable
  - File:    rotating JSON log in logs/ (machine-readable, easy to grep)
  - Each module gets a child logger via get_logger(__name__)
"""

import sys
from pathlib import Path
from loguru import logger

_CONFIGURED = False
LOG_DIR = Path(__file__).parent.parent / "logs"


def setup_logging(
    console_level: str = "INFO",
    file_level:    str = "DEBUG",
    log_dir:       Path = LOG_DIR,
) -> None:
    """
    Call once at program startup.
    Idempotent — subsequent calls are no-ops.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir.mkdir(exist_ok=True)

    # Remove default handler
    logger.remove()

    # ── Console handler (coloured, human-readable) ────────────────────────────
    logger.add(
        sys.stderr,
        level=console_level,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{extra[module]}</cyan> | "
            "{message}"
        ),
        colorize=True,
    )

    # ── Rotating file handler (JSON, 10 MB per file, keep 5) ─────────────────
    logger.add(
        log_dir / "classification_{time:YYYYMMDD}.log",
        level=file_level,
        rotation="10 MB",
        retention=5,
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {extra[module]} | {message}",
        serialize=False,
    )

    _CONFIGURED = True


def get_logger(module_name: str):
    """
    Return a loguru logger bound with the caller's module name.

    Usage:
        from utils.logger import get_logger
        log = get_logger(__name__)
        log.info("Starting ...")
    """
    setup_logging()          # safe to call multiple times
    return logger.bind(module=module_name)
