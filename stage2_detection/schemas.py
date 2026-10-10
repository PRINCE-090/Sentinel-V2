from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DetectionResult(BaseModel):
    """
    Stage 2 (Ensemble Risk Detection) output contract.
    Consumed downstream by Stage 3 (Deep Investigation) and Stage 4
    (Automated Decisioning) — field names/types here are the frozen interface
    between stages and should not be changed without a coordinated migration.
    """
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str

    lightgbm_prob: float = 0.0
    anomaly_score: float = 0.0

    drift_stage: str = "stable"
    drift_severity: int = 0

    ring_flag: bool = False
    ring_centrality: float = 0.0

    fraud_score: float = Field(default=0.0, ge=0.0, le=100.0)

    confidence_band: Literal["high", "medium", "low"] = "low"

    @field_validator("lightgbm_prob", "anomaly_score", mode="after")
    @classmethod
    def _round4(cls, v: float) -> float:
        return round(v, 4)

    @field_validator("fraud_score", mode="after")
    @classmethod
    def _round1(cls, v: float) -> float:
        return round(v, 1)
