"""Logging setup with Rich."""

import logging
import os

from rich.console import Console
from rich.logging import RichHandler

console = Console()


def _resolve_level(default: int) -> int:
    """Resolve logger level, allowing env override via KMP_LOG_LEVEL.

    Accepted values: DEBUG/INFO/WARNING/ERROR/CRITICAL (case-insensitive)
    or numeric levels (e.g. 10, 20, 30).
    """
    raw = os.getenv("KMP_LOG_LEVEL")
    if not raw:
        return default

    value = raw.strip()
    if value.isdigit():
        return int(value)

    return getattr(logging, value.upper(), default)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    effective_level = _resolve_level(level)
    if not logger.handlers:
        handler = RichHandler(console=console, show_path=False, markup=True)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(effective_level)
    return logger
