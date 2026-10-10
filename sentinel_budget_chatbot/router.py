"""
budget_advisor.router
======================
FastAPI REST endpoints for the standalone Budget Advisor microservice (Sentinel Integrated).
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware

from . import calculator
from .config import LLMProvider, Settings, get_settings
from .engine import BudgetEngine, SQLiteSessionStore
from .schemas import (
    BudgetPlan,
    BudgetingStrategy,
    CategoryAllocation,
    ChatRequest,
    ChatResponse,
    DiagnosticResponse,
    ExpenseCategory,
    FinancialProfile,
    PlanRequest,
    SessionHistoryResponse,
    SessionResetResponse,
)

logger = logging.getLogger("budget_advisor.router")

# The gateway exposes versioned APIs at ``/v1``.  The React development
# server owns the ``/api`` prefix and strips it before proxying requests to
# the gateway (see ``frontend/vite.config.ts``), so this router must not
# include ``/api`` itself.  The standalone app below adds that prefix back
# for backwards-compatible direct use on its own port.
router = APIRouter(prefix="/v1/budget", tags=["budget_advisor"])

_default_engine: BudgetEngine | None = None


def _build_llm_backend(settings: Settings):
    """
    Factory Pattern resolver that returns an LLMBackend instance matching
    the configured LLMProvider (`GROQ`, `OPENAI`, `OLLAMA`).
    """
    provider = settings.llm_provider

    if provider == LLMProvider.GROQ:
        try:
            from .adapters import GroqLLMAdapter

            return GroqLLMAdapter(
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            )
        except Exception:
            logger.exception("Failed to construct GroqLLMAdapter; falling back to deterministic advisor.")
            return None

    if provider == LLMProvider.OPENAI:
        try:
            from .adapters import OpenAILLMAdapter

            return OpenAILLMAdapter(
                api_key=settings.llm_api_key,
                model=settings.llm_model if settings.llm_model != "llama-3.3-70b-versatile" else "gpt-4o-mini",
                base_url=settings.llm_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            )
        except Exception:
            logger.exception("Failed to construct OpenAILLMAdapter; falling back to deterministic advisor.")
            return None

    if provider == LLMProvider.OLLAMA:
        try:
            from .adapters import OllamaLLMAdapter

            return OllamaLLMAdapter(
                model=settings.llm_model if settings.llm_model != "llama-3.3-70b-versatile" else "llama3.1",
                base_url=settings.llm_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            )
        except Exception:
            logger.exception("Failed to construct OllamaLLMAdapter; falling back to deterministic advisor.")
            return None

    if provider != LLMProvider.NONE:
        logger.info("LLM provider '%s' has no wired adapter; using deterministic advisor.", provider.value)

    return None


def get_budget_engine() -> BudgetEngine:
    """
    Lazily constructs a default BudgetEngine with persistent SQLite storage and factory-built LLM backend.
    """
    global _default_engine
    if _default_engine is None:
        settings = get_settings()
        llm_backend = _build_llm_backend(settings)
        sessions = SQLiteSessionStore(max_turns=settings.session_history_max_turns)
        _default_engine = BudgetEngine(settings=settings, llm_backend=llm_backend, sessions=sessions)
    return _default_engine


# ---------------------------------------------------------------------------
# Core REST Endpoints (Asynchronous)
# ---------------------------------------------------------------------------

@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Conversational budget coaching",
)
async def chat(
    request: ChatRequest,
    engine: BudgetEngine = Depends(get_budget_engine),
) -> ChatResponse:
    """
    Combines user query, profile, and goals to produce grounded financial advice.
    """
    try:
        return await engine.handle_message(request)
    except Exception as exc:
        logger.exception("Unhandled error in chat for session_id=%s", request.session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The budget advisor encountered an unexpected error processing this message.",
        ) from exc


@router.post(
    "/analyze",
    response_model=DiagnosticResponse,
    status_code=status.HTTP_200_OK,
    summary="Purely quantitative financial health check",
)
async def analyze(
    profile: FinancialProfile,
    settings: Settings = Depends(get_settings),
) -> DiagnosticResponse:
    """
    Computes deterministic financial metrics, risk flags, and 50/30/20 benchmark comparison.
    """
    try:
        metrics = calculator.compute_metrics(profile)
    except Exception as exc:
        logger.exception("Metric computation failed in analyze endpoint.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not compute financial metrics from the supplied profile.",
        ) from exc

    risk_flags = calculator.evaluate_risk_flags(
        profile=profile,
        metrics=metrics,
        housing_risk_threshold_pct=settings.housing_risk_threshold_pct,
        emergency_fund_healthy_months=settings.emergency_fund_healthy_months,
        emergency_fund_ideal_months=settings.emergency_fund_ideal_months,
    )
    comparison = calculator.benchmark_comparison(
        metrics=metrics,
        needs_pct=settings.benchmark_needs_pct,
        wants_pct=settings.benchmark_wants_pct,
        savings_pct=settings.benchmark_savings_pct,
    )

    return DiagnosticResponse(
        metrics=metrics,
        risk_flags=risk_flags,
        benchmark_comparison=comparison,
        disclaimer=settings.disclaimer_text,
    )


@router.post(
    "/plan",
    response_model=BudgetPlan,
    status_code=status.HTTP_200_OK,
    summary="Generate a customized budget allocation plan",
)
async def generate_plan(
    request: PlanRequest,
    settings: Settings = Depends(get_settings),
) -> BudgetPlan:
    """
    Produces category allocation targets and goal milestone timelines.
    """
    try:
        metrics = calculator.compute_metrics(request.profile)
    except Exception as exc:
        logger.exception("Metric computation failed in plan endpoint.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not compute financial metrics from the supplied profile.",
        ) from exc

    strategy = request.preferred_strategy
    income = request.profile.monthly_net_income
    steps: list[str] = []

    if strategy == BudgetingStrategy.AUTO:
        debt_ratio = metrics.debt_to_income_ratio or 0.0
        if debt_ratio > 0.20:
            strategy = BudgetingStrategy.DEBT_AVALANCHE
        elif metrics.net_cash_flow < 0:
            strategy = BudgetingStrategy.ZERO_BASED
        else:
            strategy = BudgetingStrategy.RULE_50_30_20

    if income <= 0:
        allocations: dict = {cat: 0.0 for cat in ExpenseCategory}
        steps.append(
            "No income was provided, so allocation percentages can't be computed. "
            "Add a monthly net income figure to generate concrete category targets."
        )
        estimated_savings = 0.0
    elif strategy == BudgetingStrategy.RULE_50_30_20:
        needs_target = round(income * settings.benchmark_needs_pct, 2)
        wants_target = round(income * settings.benchmark_wants_pct, 2)
        savings_target = round(income * settings.benchmark_savings_pct, 2)
        allocations = {
            ExpenseCategory.HOUSING: round(needs_target * 0.6, 2),
            ExpenseCategory.UTILITIES: round(needs_target * 0.15, 2),
            ExpenseCategory.FOOD: round(needs_target * 0.15, 2),
            ExpenseCategory.HEALTHCARE: round(needs_target * 0.1, 2),
            ExpenseCategory.TRANSPORTATION: round(wants_target * 0.4, 2),
            ExpenseCategory.ENTERTAINMENT: round(wants_target * 0.6, 2),
            ExpenseCategory.SAVINGS_INVESTMENT: savings_target,
            ExpenseCategory.DEBT_PAYMENT: 0.0,
            ExpenseCategory.OTHER: 0.0,
        }
        estimated_savings = savings_target
        steps.append(
            f"Target roughly {settings.benchmark_needs_pct * 100:.0f}% of income "
            f"({needs_target:,.2f}) on needs, {settings.benchmark_wants_pct * 100:.0f}% "
            f"({wants_target:,.2f}) on wants, and {settings.benchmark_savings_pct * 100:.0f}% "
            f"({savings_target:,.2f}) on savings/debt paydown."
        )
    elif strategy == BudgetingStrategy.DEBT_AVALANCHE:
        min_savings = round(income * 0.05, 2)
        debt_payment = round(max(income - metrics.fixed_expenses_total - min_savings, 0.0), 2)
        allocations = {
            ExpenseCategory.DEBT_PAYMENT: debt_payment,
            ExpenseCategory.SAVINGS_INVESTMENT: min_savings,
        }
        estimated_savings = min_savings
        steps.append(
            "Debt-to-income ratio is elevated. Prioritize paying down the "
            "highest-interest-rate debt first (avalanche method) while "
            "maintaining a small ongoing savings contribution."
        )
        steps.append(
            f"Direct approximately {debt_payment:,.2f}/month toward debt paydown "
            "above minimum payments, after fixed costs and a baseline savings cushion."
        )
    elif strategy == BudgetingStrategy.ZERO_BASED:
        remaining = income
        allocations = {}
        for item in request.profile.expenses:
            allocations[item.category] = allocations.get(item.category, 0.0) + item.amount
        allocated_total = round(sum(allocations.values()), 2)
        remaining = round(income - allocated_total, 2)
        allocations[ExpenseCategory.SAVINGS_INVESTMENT] = round(
            allocations.get(ExpenseCategory.SAVINGS_INVESTMENT, 0.0) + max(remaining, 0.0), 2
        )
        estimated_savings = max(remaining, 0.0)
        if metrics.net_cash_flow < 0:
            steps.append(
                "Current spending exceeds income. Every dollar needs a job under "
                "zero-based budgeting — start by trimming discretionary categories "
                "until total allocations no longer exceed income."
            )
        else:
            steps.append(
                "Every dollar of income is assigned a category below, including "
                "the remaining balance routed to savings."
            )
    else:  # DEBT_SNOWBALL
        min_savings = round(income * 0.05, 2)
        debt_payment = round(max(income - metrics.fixed_expenses_total - min_savings, 0.0), 2)
        allocations = {
            ExpenseCategory.DEBT_PAYMENT: debt_payment,
            ExpenseCategory.SAVINGS_INVESTMENT: min_savings,
        }
        estimated_savings = min_savings
        steps.append(
            "Prioritize paying off the smallest debt balance first for quick "
            "psychological wins (snowball method), then roll that payment into "
            "the next-smallest balance."
        )

    if metrics.net_cash_flow < 0:
        steps.insert(
            0,
            "Current expenses exceed income. This plan is a starting point — "
            "consider it a target to work toward rather than an immediate cut.",
        )

    goal_milestones: dict[str, str] = {}
    for goal in request.goals:
        contribution = calculator.required_monthly_contribution(goal)
        if contribution is None:
            goal_milestones[goal.goal_name] = "Target date has already passed — consider setting a new date."
        elif contribution == 0:
            goal_milestones[goal.goal_name] = "Goal is already fully funded."
        else:
            goal_milestones[goal.goal_name] = (
                f"Needs about {contribution:,.2f}/month to reach "
                f"{goal.target_amount:,.2f} by {goal.target_date.date()}."
            )

    return BudgetPlan(
        strategy_used=strategy,
        recommended_allocations=allocations,
        estimated_monthly_savings=estimated_savings,
        actionable_steps=steps,
        goal_milestones=goal_milestones,
        disclaimer=settings.disclaimer_text,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve conversation history for a session",
)
async def get_session_history(
    session_id: str,
    engine: BudgetEngine = Depends(get_budget_engine),
) -> SessionHistoryResponse:
    try:
        messages = engine.get_history(session_id)
    except Exception as exc:
        logger.exception("Unhandled error in get_session_history for session_id=%s", session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not retrieve session history due to an internal error.",
        ) from exc

    return SessionHistoryResponse(session_id=session_id, messages=messages)


@router.delete(
    "/sessions/{session_id}",
    response_model=SessionResetResponse,
    status_code=status.HTTP_200_OK,
    summary="Clear conversational memory for a session",
)
async def reset_session(
    session_id: str,
    engine: BudgetEngine = Depends(get_budget_engine),
) -> SessionResetResponse:
    try:
        existed = engine.reset_session(session_id)
    except Exception as exc:
        logger.exception("Unhandled error in reset_session for session_id=%s", session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not clear session due to an internal error.",
        ) from exc

    return SessionResetResponse(
        session_id=session_id,
        cleared=existed,
        detail="Session memory cleared." if existed else "No active session found; nothing to clear.",
    )


@router.get(
    "/presets",
    summary="Get preset financial profiles for instant hackathon demoing",
)
async def get_preset_profiles() -> dict:
    return {
        "young_professional": {
            "monthly_net_income": 4800.0,
            "expenses": [
                {"name": "Rent & Utilities", "amount": 1800.0, "category": "HOUSING", "is_fixed": True},
                {"name": "Student Loan Minimum", "amount": 400.0, "category": "DEBT_PAYMENT", "is_fixed": True},
                {"name": "Groceries", "amount": 450.0, "category": "FOOD", "is_fixed": False},
                {"name": "Dining & Nightlife", "amount": 550.0, "category": "ENTERTAINMENT", "is_fixed": False},
                {"name": "Streaming & Gym", "amount": 150.0, "category": "ENTERTAINMENT", "is_fixed": False},
                {"name": "401k Contribution", "amount": 250.0, "category": "SAVINGS_INVESTMENT", "is_fixed": True},
            ],
            "total_savings": 3500.0,
            "total_debt": 18000.0,
            "currency": "USD",
        },
        "family_in_deficit": {
            "monthly_net_income": 6200.0,
            "expenses": [
                {"name": "Mortgage & Property Tax", "amount": 3100.0, "category": "HOUSING", "is_fixed": True},
                {"name": "Childcare & Schooling", "amount": 1400.0, "category": "OTHER", "is_fixed": True},
                {"name": "Family Groceries", "amount": 900.0, "category": "FOOD", "is_fixed": False},
                {"name": "Auto Loan & Insurance", "amount": 650.0, "category": "TRANSPORTATION", "is_fixed": True},
                {"name": "Utilities & Internet", "amount": 400.0, "category": "UTILITIES", "is_fixed": True},
            ],
            "total_savings": 2000.0,
            "total_debt": 240000.0,
            "currency": "USD",
        },
        "freelancer_building_buffer": {
            "monthly_net_income": 3500.0,
            "expenses": [
                {"name": "Studio Rent", "amount": 1100.0, "category": "HOUSING", "is_fixed": True},
                {"name": "Software Subscriptions", "amount": 200.0, "category": "OTHER", "is_fixed": True},
                {"name": "Food & Supplies", "amount": 400.0, "category": "FOOD", "is_fixed": False},
                {"name": "Health Insurance", "amount": 350.0, "category": "HEALTHCARE", "is_fixed": True},
            ],
            "total_savings": 800.0,
            "total_debt": 0.0,
            "currency": "USD",
        },
    }


# ---------------------------------------------------------------------------
# Standalone ASGI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Budget Advisor (Sentinel Integrated)",
    description="Conversational personal budget advisor and financial planner microservice.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health", tags=["meta"])
async def health_check() -> dict:
    return {"status": "ok", "service": get_settings().service_name}
