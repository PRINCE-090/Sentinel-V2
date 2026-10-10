"""
budget_advisor.config
=======================
Environment and model configuration for the standalone Budget Advisor
microservice.

Uses pydantic-settings so configuration can be supplied via environment
variables (prefixed `BUDGET_ADVISOR_`) or a `.env` file, with sane
defaults so the service is runnable out of the box with zero setup
(falling back to the deterministic, non-LLM advisor — see engine.py).

This module has no dependency on any other package. It is safe to import
standalone.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    """Supported LLM backends. Engine is agnostic to which one is active."""

    NONE = "none"
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"


class Settings(BaseSettings):
    """
    Central settings object. Instantiate once via `get_settings()` rather
    than constructing directly, so the whole app shares one (cached)
    config instance.
    """

    model_config = SettingsConfigDict(
        env_prefix="BUDGET_ADVISOR_",
        env_file=Path(__file__).with_name(".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Service metadata -------------------------------------------------
    service_name: str = "budget_advisor"
    environment: str = Field(default="development", description="development | staging | production")
    debug: bool = False

    # --- LLM backend selection ---------------------------------------------
    llm_provider: LLMProvider = LLMProvider.NONE
    llm_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Model name/identifier passed to whichever provider is active "
        "(e.g. 'llama-3.3-70b-versatile' for Groq, 'llama3.1' for Ollama, "
        "'gpt-4o-mini' for OpenAI, 'claude-sonnet-5' for Anthropic).",
    )
    llm_api_key: Optional[str] = Field(
        default=None,
        description="API key for Groq/OpenAI/Anthropic. Not required for Ollama "
        "(local) or when llm_provider is NONE. For Groq, this is the GROQ_API_KEY "
        "value (can also be supplied via the standard GROQ_API_KEY env var, which "
        "the Groq SDK reads automatically if BUDGET_ADVISOR_LLM_API_KEY is unset).",
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        description="Override base URL. Defaults to Groq's OpenAI-compatible "
        "endpoint ('https://api.groq.com/openai/v1') when llm_provider is GROQ "
        "and no override is given; otherwise a local Ollama server "
        "('http://localhost:11434') or a self-hosted OpenAI-compatible endpoint.",
    )
    llm_timeout_seconds: float = Field(default=20.0, gt=0)
    llm_max_tokens: int = Field(default=800, gt=0)
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)

    # --- Financial planning defaults / benchmarks ---------------------------
    # These are the canonical "50/30/20" style benchmark targets used by
    # the analyze endpoint's risk-flagging logic. Expressed as fraction of
    # net income. Kept configurable so deployments can tune thresholds
    # without code changes.
    benchmark_needs_pct: float = Field(default=0.50, ge=0.0, le=1.0)
    benchmark_wants_pct: float = Field(default=0.30, ge=0.0, le=1.0)
    benchmark_savings_pct: float = Field(default=0.20, ge=0.0, le=1.0)

    housing_risk_threshold_pct: float = Field(
        default=0.30, ge=0.0, le=1.0,
        description="Housing spend above this fraction of income is flagged as a risk.",
    )
    emergency_fund_healthy_months: float = Field(
        default=3.0, gt=0,
        description="Minimum months of runway considered a healthy emergency fund.",
    )
    emergency_fund_ideal_months: float = Field(
        default=6.0, gt=0,
        description="Months of runway considered an ideal, fully-funded emergency fund.",
    )

    # --- Session store ------------------------------------------------------
    session_history_max_turns: int = Field(
        default=50, gt=0,
        description="Max turns retained per session in the default in-memory store "
        "before oldest turns are evicted.",
    )

    # --- Standard disclaimer -------------------------------------------------
    disclaimer_text: str = (
        "This assistant provides educational financial planning guidance and "
        "is not a certified financial advisor."
    )


_settings_singleton: Optional[Settings] = None


def get_settings() -> Settings:
    """Returns a process-wide cached Settings instance."""
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings()
    return _settings_singleton
