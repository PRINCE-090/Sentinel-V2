from __future__ import annotations
from datetime import datetime
from typing import List, Dict, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stage2_detection.schemas import DetectionResult

# Allowed investigator review states. "Needs Review" is the default for
# any case that reaches Stage 3 (i.e. was flagged), matching the
# existing investigation_priority default of "LOW" for cases untouched
# by a human yet.
ReviewStatus = Literal["Suspected", "Verified", "Needs Review"]


class ReasonCode(BaseModel):
    """A single SHAP-derived, human-readable explanation for a risk contribution."""
    model_config = ConfigDict(from_attributes=True)

    feature_name: str
    label: str
    value: float
    impact: Literal["positive", "negative"]  # "positive" = increases risk, "negative" = decreases risk
    attribution_score: float
    human_description: str

    @field_validator("attribution_score", mode="after")
    @classmethod
    def _round4(cls, v: float) -> float:
        return round(v, 4)


class TypologyMatch(BaseModel):
    """A matched money-laundering typology from the Typology DNA matcher."""
    model_config = ConfigDict(from_attributes=True)

    typology_name: str
    fatf_code: str
    confidence: float
    description: str


class ProceedsHop(BaseModel):
    """A single hop in the reconstructed fund-flow trail."""
    model_config = ConfigDict(from_attributes=True)

    hop_number: int
    from_account: str
    to_account: str
    amount: float
    timestamp_delta_hours: float
    account_metadata: Dict[str, str] = Field(default_factory=dict)


class GraphSummary(BaseModel):
    """Summary of the graph fund-flow analysis around the transacting accounts."""
    model_config = ConfigDict(from_attributes=True)

    mule_ring_flag: bool = False
    centrality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    cycle_detected: bool = False
    cycle_nodes: List[str] = Field(default_factory=list)
    neighbor_count: int = 0


class InvestigationDossier(BaseModel):
    """
    Stage 3 (Deep Investigation) output contract — the unified case file
    consumed by Stage 4 (Automated Decisioning) and Stage 5 (Regulatory
    Compliance / SAR drafting).
    """
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    timestamp: str
    sender_account: str
    receiver_account: str
    amount: float

    detection_result: DetectionResult
    graph_summary: GraphSummary

    top_reason_codes: List[ReasonCode] = Field(default_factory=list)
    typology_matches: List[TypologyMatch] = Field(default_factory=list)
    proceeds_trail: List[ProceedsHop] = Field(default_factory=list)

    llm_sar_narrative: Optional[str] = None
    investigation_priority: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = "LOW"
    sar_suggested: bool = False

    # --- Investigator annotation fields ---
    # These are purely human-supplied metadata. They are never read by
    # DecisionEngine, the scorers, or SAR synthesis, and updating them
    # must never re-trigger any of those. Keep it that way — any future
    # code that branches pipeline behavior on review_status /
    # investigator_note is a bug.
    review_status: ReviewStatus = "Needs Review"
    investigator_note: Optional[str] = Field(default=None, max_length=2000)
    updated_at: Optional[datetime] = None
