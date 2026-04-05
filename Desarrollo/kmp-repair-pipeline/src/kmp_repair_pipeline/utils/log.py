"""Logging setup with Rich."""

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console()


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(console=console, show_path=False, markup=True)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger
