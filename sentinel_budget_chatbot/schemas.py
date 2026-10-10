"""
budget_advisor.schemas
========================
Pydantic v2 data models for the Budget Advisor microservice: financial
profile inputs, goal tracking, budget plan outputs, and chat API
contracts.

All request/response models use `model_config = ConfigDict(extra="forbid")`
for strict payload validation — unexpected fields fail fast at the API
boundary.

This module has zero external dependencies beyond pydantic itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ExpenseCategory(str, Enum):
    HOUSING = "HOUSING"
    UTILITIES = "UTILITIES"
    FOOD = "FOOD"
    TRANSPORTATION = "TRANSPORTATION"
    HEALTHCARE = "HEALTHCARE"
    DEBT_PAYMENT = "DEBT_PAYMENT"
    ENTERTAINMENT = "ENTERTAINMENT"
    SAVINGS_INVESTMENT = "SAVINGS_INVESTMENT"
    OTHER = "OTHER"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class RiskLevel(str, Enum):
    """Severity for a diagnostic flag raised during financial health checks."""

    OK = "ok"
    WATCH = "watch"
    AT_RISK = "at_risk"
    CRITICAL = "critical"


class BudgetingStrategy(str, Enum):
    """Named strategies the plan generator can be steered toward."""

    RULE_50_30_20 = "50_30_20"
    ZERO_BASED = "zero_based"
    DEBT_SNOWBALL = "debt_snowball"
    DEBT_AVALANCHE = "debt_avalanche"
    AUTO = "auto"  # engine picks a strategy based on the profile


class WidgetType(str, Enum):
    """Supported interactive frontend UI widgets."""

    PIE_CHART = "PIE_CHART"
    PAYOFF_TIMELINE = "PAYOFF_TIMELINE"
    GROWTH_CHART = "GROWTH_CHART"
    ACTION_CHECKLIST = "ACTION_CHECKLIST"


class UIWidget(BaseModel):
    """Structured UI payload hint for frontend rendering."""

    model_config = ConfigDict(extra="forbid")

    widget_type: WidgetType
    title: str
    data: Dict[str, Any]


# ---------------------------------------------------------------------------
# Financial Profile models
# ---------------------------------------------------------------------------

class ExpenseItem(BaseModel):
    """A single recurring or one-off expense line item."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    amount: float = Field(..., ge=0)
    category: ExpenseCategory
    is_fixed: bool = Field(
        ..., description="True for fixed/committed costs (rent, loan payments); "
        "False for discretionary/variable spending."
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be empty or whitespace-only")
        return stripped


class FinancialProfile(BaseModel):
    """A user's complete financial snapshot for a given period (monthly)."""

    model_config = ConfigDict(extra="forbid")

    monthly_net_income: float = Field(..., ge=0)
    expenses: List[ExpenseItem] = Field(default_factory=list)
    total_savings: float = Field(..., ge=0)
    total_debt: float = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _uppercase_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# Goal & Planning models
# ---------------------------------------------------------------------------

class FinancialGoal(BaseModel):
    """A single savings/financial target with a deadline."""

    model_config = ConfigDict(extra="forbid")

    goal_name: str = Field(..., min_length=1, max_length=200)
    target_amount: float = Field(..., gt=0)
    current_amount: float = Field(default=0.0, ge=0)
    target_date: datetime

    @field_validator("goal_name")
    @classmethod
    def _strip_goal_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("goal_name must not be empty or whitespace-only")
        return stripped

    @model_validator(mode="after")
    def _current_not_exceed_target_warning_free(self) -> "FinancialGoal":
        # Not an error — a goal can be exceeded (e.g. tracking overshoot) —
        # but we normalize negative-progress nonsense defensively upstream
        # via ge=0 on current_amount. No-op validator kept for clarity /
        # future extension (e.g. auto-complete flag).
        return self


class CategoryAllocation(BaseModel):
    """One row of a recommended budget allocation."""

    model_config = ConfigDict(extra="forbid")

    category: ExpenseCategory
    amount: float = Field(..., ge=0)
    percent_of_income: float = Field(..., ge=0)


class BudgetPlan(BaseModel):
    """Output of the automated plan generator."""

    model_config = ConfigDict(extra="forbid")

    strategy_used: BudgetingStrategy
    recommended_allocations: Dict[ExpenseCategory, float] = Field(
        default_factory=dict,
        description="Category -> recommended monthly dollar amount.",
    )
    estimated_monthly_savings: float
    actionable_steps: List[str] = Field(default_factory=list)
    goal_milestones: Dict[str, str] = Field(
        default_factory=dict,
        description="Goal name -> plain-language milestone/timeline summary "
        "(e.g. 'On track for Dec 2027 at current savings rate').",
    )
    disclaimer: str = ""


# ---------------------------------------------------------------------------
# Diagnostic (analyze endpoint) models
# ---------------------------------------------------------------------------

class RiskFlag(BaseModel):
    """A single diagnostic finding raised during a financial health check."""

    model_config = ConfigDict(extra="forbid")

    category: Optional[ExpenseCategory] = None
    level: RiskLevel
    message: str
    metric_value: Optional[float] = None
    benchmark_value: Optional[float] = None


class FinancialMetrics(BaseModel):
    """Deterministically computed metrics — see calculator.py."""

    model_config = ConfigDict(extra="forbid")

    total_monthly_expenses: float
    fixed_expenses_total: float
    discretionary_expenses_total: float
    net_cash_flow: float
    savings_rate_pct: float
    expense_breakdown_pct: Dict[ExpenseCategory, float]
    emergency_fund_runway_months: Optional[float] = Field(
        default=None,
        description="None when monthly expenses are zero (undefined runway).",
    )
    debt_to_income_ratio: Optional[float] = Field(
        default=None,
        description="Total debt / annualized net income. None if income is zero.",
    )


class DiagnosticResponse(BaseModel):
    """Response for POST /api/v1/budget/analyze."""

    model_config = ConfigDict(extra="forbid")

    metrics: FinancialMetrics
    risk_flags: List[RiskFlag] = Field(default_factory=list)
    benchmark_comparison: Dict[str, Any] = Field(
        default_factory=dict,
        description="50/30/20-style benchmark comparison summary.",
    )
    disclaimer: str = ""


# ---------------------------------------------------------------------------
# Chat & API payload models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    """A single turn in a conversation thread."""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str = Field(..., min_length=1, max_length=8000)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("content")
    @classmethod
    def _strip_content(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("content must not be empty or whitespace-only")
        return stripped


class ChatRequest(BaseModel):
    """Inbound payload for POST /api/v1/budget/chat."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str = Field(..., min_length=1, max_length=4000)
    profile: Optional[FinancialProfile] = None
    goals: Optional[List[FinancialGoal]] = None
    history: List[ChatMessage] = Field(default_factory=list)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("query must not be empty or whitespace-only")
        return stripped


class DebtPayoffMonth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: int
    remaining_balance: float
    interest_paid: float
    principal_paid: float


class DebtPayoffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: BudgetingStrategy
    months_to_payoff: int
    total_interest_paid: float
    total_amount_paid: float
    monthly_schedule: List[DebtPayoffMonth] = Field(default_factory=list)


class CompoundGrowthYear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    total_balance: float
    total_principal: float
    total_interest: float


class CompoundGrowthResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_contribution: float
    annual_return_pct: float
    years: int
    final_balance: float
    total_principal: float
    total_interest_earned: float
    yearly_breakdown: List[CompoundGrowthYear] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Outbound payload for POST /api/v1/budget/chat."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    reply: str
    financial_metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Deterministically computed metrics that grounded this "
        "reply, when a profile was supplied.",
    )
    warnings: List[str] = Field(default_factory=list)
    widgets: List[UIWidget] = Field(default_factory=list)
    pii_redactions: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan generator request wrapper
# ---------------------------------------------------------------------------

class PlanRequest(BaseModel):
    """Request body for POST /api/v1/budget/plan."""

    model_config = ConfigDict(extra="forbid")

    profile: FinancialProfile
    goals: List[FinancialGoal] = Field(default_factory=list)
    preferred_strategy: BudgetingStrategy = BudgetingStrategy.AUTO


# ---------------------------------------------------------------------------
# Session models
# ---------------------------------------------------------------------------

class SessionHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    messages: List[ChatMessage] = Field(default_factory=list)


class SessionResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    cleared: bool
    detail: str = "Session memory cleared."
