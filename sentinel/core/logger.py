"""
sentinel.core.logger

Structured logging setup for Sentinel. All stages should obtain their
logger via `get_logger(__name__)` rather than calling `logging.getLogger`
directly, so log formatting stays consistent across the pipeline and
handler attachment is idempotent under reload (e.g. uvicorn's --reload).
"""

from __future__ import annotations

import logging
import sys
from typing import Dict

_LOG_FORMAT = "[%(asctime)s] %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Cache of loggers we've already configured, keyed by name. Prevents
# duplicate handlers (and therefore duplicate log lines) if get_logger()
# is called more than once for the same name, which happens routinely
# under module reload.
_configured_loggers: Dict[str, logging.Logger] = {}


def _build_stdout_handler() -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
    return handler


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Return a configured logger that writes structured lines to stdout.

    Args:
        name: Typically `__name__` of the calling module, e.g.
            "stage4_decisioning.decision_engine".
        level: Logging level for this logger. Defaults to INFO. Callers
            that need DEBUG output for local debugging can pass
            logging.DEBUG explicitly.

    Returns:
        A `logging.Logger` with exactly one stdout `StreamHandler`
        attached, regardless of how many times this function is called
        for the same `name`.
    """
    if name in _configured_loggers:
        return _configured_loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(level)
    # Don't propagate to the root logger — otherwise, if the host app
    # (e.g. uvicorn) also configures the root logger, every line would
    # be emitted twice.
    logger.propagate = False

    if not logger.handlers:
        logger.addHandler(_build_stdout_handler())

    _configured_loggers[name] = logger
    return logger
