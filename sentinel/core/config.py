"""
sentinel.core.config

Central runtime configuration for Sentinel, sourced from environment
variables (via `python-dotenv`) with sensible local-dev defaults.

All env var names are namespaced with a SENTINEL_ prefix so they don't
collide with unrelated variables in a shared deployment environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Loads variables from a .env file in the current working directory (if
# present) into os.environ. Safe to call even if no .env file exists —
# it's then a no-op. Existing environment variables always take
# precedence over .env file values.
load_dotenv()


def _get_str(key: str, default: str) -> str:
    return os.getenv(key, default)


def _get_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class SentinelConfig:
    """
    Immutable-in-practice snapshot of Sentinel's runtime configuration.

    A single instance (`CONFIG`, below) is constructed at import time.
    Because values are read once at process startup, changing an
    environment variable at runtime has no effect until the process is
    restarted — this is deliberate, so config never silently changes
    mid-request for a running pipeline.
    """

    # --- Stage 3: Ollama LLM SAR-narrative synthesis ---
    ollama_base_url: str = field(
        default_factory=lambda: _get_str("SENTINEL_OLLAMA_BASE_URL", "http://localhost:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: _get_str("SENTINEL_OLLAMA_MODEL", "llama3.1:latest")
    )
    ollama_timeout_seconds: float = field(
        default_factory=lambda: _get_float("SENTINEL_OLLAMA_TIMEOUT_SECONDS", 120.0)
    )

    # --- Compliance thresholds ---
    reporting_threshold: float = field(
        default_factory=lambda: _get_float("SENTINEL_REPORTING_THRESHOLD", 10000.0)
    )

    # --- Cross-stage fraud-score risk bands (single source of truth) ---
    # Stage 3 (investigation_pipeline._determine_priority) assigns
    # LOW/MEDIUM/HIGH/CRITICAL investigation priority off these same
    # thresholds, and Stage 4 (decision_engine.DecisionEngine) gates its
    # RELEASE/MONITOR/HOLD/BLOCK tiers off them too. Previously these lived
    # as separately hardcoded numbers in each stage and had drifted out of
    # sync — Stage 3 started a MEDIUM band at 30.0 but Stage 4 had no rule
    # below 60.0, so any score in [30.0, 60.0) fell through every decision
    # rule and silently defaulted to RELEASE. Both stages must import these
    # constants rather than hardcoding their own copies.
    risk_threshold_medium: float = field(
        default_factory=lambda: _get_float("SENTINEL_RISK_THRESHOLD_MEDIUM", 30.0)
    )
    risk_threshold_high: float = field(
        default_factory=lambda: _get_float("SENTINEL_RISK_THRESHOLD_HIGH", 60.0)
    )
    risk_threshold_critical: float = field(
        default_factory=lambda: _get_float("SENTINEL_RISK_THRESHOLD_CRITICAL", 85.0)
    )

    # --- Stage 2: model artifact paths ---
    lgb_model_path: str = field(
        default_factory=lambda: _get_str("SENTINEL_LGB_MODEL_PATH", "models/lightgbm_scorer.txt")
    )
    anomaly_model_path: str = field(
        default_factory=lambda: _get_str("SENTINEL_ANOMALY_MODEL_PATH", "models/anomaly_scorer.pkl")
    )

    # --- Stage 1: data store paths ---
    kyc_store_path: str = field(
        default_factory=lambda: _get_str("SENTINEL_KYC_STORE_PATH", "data/kyc_profiles.json")
    )

    # --- Stage 3: typology matching (TypologyDNA) ---
    typology_model: str = field(
        default_factory=lambda: _get_str("SENTINEL_TYPOLOGY_MODEL", "all-MiniLM-L6-v2")
    )
    typology_top_k: int = field(
        default_factory=lambda: _get_int("SENTINEL_TYPOLOGY_TOP_K", 5)
    )

    # --- Service / gateway metadata ---
    service_name: str = field(
        default_factory=lambda: _get_str("SENTINEL_SERVICE_NAME", "Sentinel AML Gateway")
    )
    service_version: str = field(
        default_factory=lambda: _get_str("SENTINEL_SERVICE_VERSION", "1.0.0")
    )
    log_level: str = field(
        default_factory=lambda: _get_str("SENTINEL_LOG_LEVEL", "INFO")
    )

    # --- Stage 1: declared-income / occupation strain tuning ---
    # Monthly income anchor per declared_income_band, used to detect
    # transfers disproportionate to a sender's *declared* income. Overridable
    # per-env so thresholds can be retuned (inflation, market changes)
    # without a code change/redeploy.
    income_band_anchors: dict = field(default_factory=lambda: {
        "LOW": _get_float("SENTINEL_INCOME_ANCHOR_LOW", 30_000.0),
        "MEDIUM": _get_float("SENTINEL_INCOME_ANCHOR_MEDIUM", 100_000.0),
        "HIGH": _get_float("SENTINEL_INCOME_ANCHOR_HIGH", 500_000.0),
        "ULTRA": _get_float("SENTINEL_INCOME_ANCHOR_ULTRA", 2_000_000.0),
    })
    income_anchor_default: float = field(
        default_factory=lambda: _get_float("SENTINEL_INCOME_ANCHOR_DEFAULT", 100_000.0)
    )

    # Occupation-risk multiplier applied to the income ratio: a large
    # multiple of declared income is far more anomalous for an individual
    # (salaried/student) than for a business/high-net-worth entity with
    # irregular, lumpy cash flows.
    occupation_strain_factors: dict = field(default_factory=lambda: {
        "SALARIED": _get_float("SENTINEL_STRAIN_FACTOR_SALARIED", 2.5),
        "STUDENT": _get_float("SENTINEL_STRAIN_FACTOR_STUDENT", 2.5),
        "BUSINESS": _get_float("SENTINEL_STRAIN_FACTOR_BUSINESS", 0.7),
        "HIGH_NET_WORTH": _get_float("SENTINEL_STRAIN_FACTOR_HIGH_NET_WORTH", 0.7),
    })
    occupation_strain_factor_default: float = field(
        default_factory=lambda: _get_float("SENTINEL_STRAIN_FACTOR_DEFAULT", 1.5)
    )

    # --- Misc runtime flags ---
    debug: bool = field(default_factory=lambda: _get_bool("SENTINEL_DEBUG", False))


# Single shared config instance, constructed once at import time.
# NOTE: this depends on `sentinel/core/config.py` alone — it intentionally
# does not know about any stageN_* package, keeping the dependency graph
# one-directional (stages depend on core, core depends on nothing above it).
CONFIG = SentinelConfig()
