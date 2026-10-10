"""
stage5_gateway/schemas.py

Strict inbound and outbound API contracts for the Sentinel Stage 5 API
Gateway. These models define the REST boundary between the core banking
system and the Sentinel AML pipeline.
"""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator

from stage3_investigation.schemas import ReviewStatus


class TransactionRequest(BaseModel):
    """
    Inbound payload submitted by the core banking system for a single
    transaction to be evaluated by the Sentinel pipeline.

    `extra="forbid"` is set deliberately: any unexpected or bloated
    fields in the incoming payload should cause a hard validation error
    rather than being silently dropped, so malformed integrations are
    caught immediately at the gateway boundary.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for the transaction, assigned by the core banking system.",
    )
    sender_account: str = Field(
        ...,
        min_length=1,
        description="Account number or identifier of the sending party.",
    )
    receiver_account: str = Field(
        ...,
        min_length=1,
        description="Account number or identifier of the receiving party.",
    )
    amount: float = Field(
        ...,
        gt=0,
        description="Transaction amount. Must be strictly greater than zero.",
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp at which the transaction was initiated.",
    )
    channel: str = Field(
        ...,
        description='Payment rail used for the transaction (e.g. "UPI", "NEFT", "RTGS", "SWIFT").',
    )
    device_fingerprint_hash: Optional[str] = Field(
        default="",
        description="Hashed device fingerprint of the originating client, if available.",
    )
    ip_geolocation: Optional[str] = Field(
        default="IN",
        description="ISO country code derived from the originating IP address.",
    )
    merchant_category_code: Optional[str] = Field(
        default="0000",
        description="Merchant category code (MCC) associated with the transaction, if applicable.",
    )


class EvaluationResponse(BaseModel):
    """
    Outbound payload returned to the core banking system once the
    Sentinel pipeline has finished evaluating a transaction.

    Represents the final operational verdict (action) along with the
    supporting fraud score, primary triggering reason, and — for highly
    suspicious transactions — the synthesized SAR narrative.
    """

    transaction_id: str = Field(
        ...,
        description="Unique identifier for the evaluated transaction.",
    )
    action: Literal["BLOCK", "HOLD", "MONITOR", "RELEASE"] = Field(
        ...,
        description=(
            'The operational decision, constrained to Stage 4\'s exact '
            'output set. If Stage 4 ever emits an unexpected value, this '
            'schema will fail validation rather than let a malformed '
            'response reach the core banking system.'
        ),
    )
    fraud_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Model-assigned fraud score for the transaction, ranging from 0.0 to 100.0.",
    )
    primary_reason: str = Field(
        ...,
        description="The name of the primary rule that triggered the returned action.",
    )
    requires_manual_review: bool = Field(
        ...,
        description="Whether a compliance analyst must manually review this transaction before further processing.",
    )
    sar_narrative: Optional[str] = Field(
        default=None,
        description=(
            "LLM-synthesized Suspicious Activity Report narrative, present "
            "only when the transaction was flagged as highly suspicious. "
            "None otherwise."
        ),
    )


class CaseReviewUpdateRequest(BaseModel):
    """
    Inbound payload for an investigator updating a case's review status
    and/or note.

    `extra="forbid"` for the same reason as TransactionRequest — reject
    malformed payloads at the boundary rather than silently ignoring
    unknown fields. At least one of the two fields must be supplied;
    enforced via `has_updates()` at the API layer so the 400 response
    can carry a clear message rather than a generic validation error.
    """

    model_config = ConfigDict(extra="forbid")

    review_status: Optional[ReviewStatus] = Field(
        default=None,
        description="New review status for the case.",
    )
    investigator_note: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Free-text investigator note attached to the case.",
    )

    @field_validator("investigator_note", mode="after")
    @classmethod
    def _strip_note(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v is not None else v

    def has_updates(self) -> bool:
        """True if the request actually carries at least one field to update."""
        return self.review_status is not None or self.investigator_note is not None


class CaseDetailResponse(BaseModel):
    """
    Outbound payload describing the current state of a case: the
    original pipeline verdict (action, fraud_score, etc. — a frozen
    snapshot from when the transaction was evaluated) plus whatever
    investigator annotations have been applied since.
    """

    transaction_id: str = Field(
        ...,
        description="Unique identifier for the case's underlying transaction.",
    )
    action: Literal["BLOCK", "HOLD", "MONITOR", "RELEASE"] = Field(
        ...,
        description="The operational decision recorded at evaluation time.",
    )
    fraud_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Model-assigned fraud score recorded at evaluation time.",
    )
    primary_reason: str = Field(
        ...,
        description="The name of the primary rule that triggered the recorded action.",
    )
    requires_manual_review: bool = Field(
        ...,
        description="Whether a compliance analyst must manually review this transaction.",
    )
    sar_narrative: Optional[str] = Field(
        default=None,
        description="LLM-synthesized SAR narrative, if one was generated at evaluation time.",
    )

    investigation_priority: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(
        ...,
        description="Stage 3 investigation priority recorded at evaluation time.",
    )

    review_status: ReviewStatus = Field(
        ...,
        description="Current investigator-assigned review status for this case.",
    )
    investigator_note: Optional[str] = Field(
        default=None,
        description="Current free-text investigator note attached to this case.",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the last investigator annotation update, if any.",
    )


class CaseListResponse(BaseModel):
    """
    Outbound payload for the filtered/paginated review queue
    (`GET /v1/cases`). `total` is the total number of cases matching the
    applied filters (independent of pagination), so clients can compute
    page counts; `items` is just the current page.
    """

    items: List[CaseDetailResponse] = Field(
        default_factory=list,
        description="The page of cases matching the applied filters.",
    )
    total: int = Field(
        ...,
        ge=0,
        description="Total number of cases matching the applied filters, across all pages.",
    )
    limit: int = Field(
        ...,
        description="The page size used for this response.",
    )
    offset: int = Field(
        ...,
        description="The offset used for this response.",
    )


class DashboardSummaryResponse(BaseModel):
    """
    Outbound payload for `GET /v1/dashboard/summary` — aggregated counts
    driving the review-dashboard badges. Purely a read/aggregation over
    CaseStore's current contents; computing it never touches the
    detection/decisioning pipeline.
    """

    total_cases: int = Field(
        ...,
        ge=0,
        description="Total number of cases currently in the store.",
    )
    by_review_status: Dict[str, int] = Field(
        default_factory=dict,
        description=(
            'Case counts keyed by review_status, e.g. '
            '{"Needs Review": 5, "Suspected": 2, "Verified": 1}. '
            "All three ReviewStatus values are always present, even if zero."
        ),
    )
    by_action: Dict[str, int] = Field(
        default_factory=dict,
        description=(
            'Case counts keyed by action, e.g. '
            '{"BLOCK": 1, "HOLD": 3, "MONITOR": 2, "RELEASE": 4}. '
            "All four action values are always present, even if zero."
        ),
    )
    by_priority: Dict[str, int] = Field(
        default_factory=dict,
        description=(
            'Case counts keyed by investigation_priority, e.g. '
            '{"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}. '
            "All four priority values are always present, even if zero."
        ),
    )
    high_priority_pending_count: int = Field(
        ...,
        ge=0,
        description=(
            'Count of cases still review_status="Needs Review" that are '
            'also either action in {"BLOCK", "HOLD"} or investigation_priority '
            'in {"CRITICAL", "HIGH"} — the set that most urgently needs an '
            "investigator's attention."
        ),
    )
