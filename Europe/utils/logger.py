"""
Logging configuration using loguru.
"""
import sys
from pathlib import Path
from loguru import logger

import sys as _sys
_sys.path.insert(0, str(__file__).rsplit("\\", 2)[0])
from config.settings import LOG_FORMAT, LOG_LEVEL


def setup_logger(
    name: str = "scraper",
    log_dir: Path = None,
    level: str = LOG_LEVEL,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> "logger":
    """
    Configure and return a logger instance.
    
    Args:
        name: Logger name (used for log file naming)
        log_dir: Directory for log files (optional)
        level: Minimum log level
        rotation: When to rotate log files
        retention: How long to keep log files
        
    Returns:
        Configured logger instance
    """
    # Remove default handler
    logger.remove()
    
    # Add console handler with colors
    logger.add(
        sys.stderr,
        format=LOG_FORMAT,
        level=level,
        colorize=True,
    )
    
    # Add file handler if log_dir is specified
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f"{name}.log"
        logger.add(
            log_file,
            format=LOG_FORMAT.replace("<green>", "").replace("</green>", "")
                   .replace("<level>", "").replace("</level>", "")
                   .replace("<cyan>", "").replace("</cyan>", ""),
            level=level,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
        )
        
        logger.info(f"Logging to {log_file}")
    
    return logger


def get_logger(name: str = None) -> "logger":
    """
    Get a logger instance with an optional name context.
    
    Args:
        name: Optional name to bind to logger
        
    Returns:
        Logger instance
    """
    if name:
        return logger.bind(name=name)
    return logger
