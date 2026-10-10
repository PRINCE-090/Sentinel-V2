"""
stage4_decisioning/schemas.py

Output schemas for the Stage 4 automated decisioning engine.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, List


class DecisionReason(BaseModel):
    """A single risk factor / rule that contributed to a decision."""

    rule_name: str
    description: str


class ActionDirective(BaseModel):
    """
    The final operational directive produced by the Stage 4 decision
    engine for a single transaction.
    """

    transaction_id: str
    action: Literal["BLOCK", "HOLD", "MONITOR", "RELEASE"]
    primary_reason: str
    all_reasons: List[DecisionReason] = Field(default_factory=list)
    requires_manual_review: bool

    model_config = ConfigDict(from_attributes=True)
