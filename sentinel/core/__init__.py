"""
sentinel.core

Shared, low-level utilities used across every stage of the Sentinel AML
pipeline (ingestion, detection, investigation, decisioning, gateway).

This package must not import from any `stageN_*` package or from
`stage5_gateway` — it sits at the bottom of the dependency graph and
everything else depends on it, never the other way around.
"""

from .logger import get_logger
from .config import CONFIG

__all__ = ["get_logger", "CONFIG"]
