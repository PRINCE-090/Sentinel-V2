"""
budget_advisor
================
Standalone, production-ready conversational Personal Budget Advisor and
Financial Planner microservice.

Zero dependencies on any external fraud/AML/banking-detection pipeline —
this package is fully self-contained and importable/runnable on its own.

Run standalone:

    uvicorn budget_advisor.router:app --reload

Or mount into a larger FastAPI app:

    from fastapi import FastAPI
    from budget_advisor import budget_router, BudgetEngine, get_budget_engine

    app = FastAPI()
    app.include_router(budget_router)

    # Optionally inject a real LLM backend + persistent session store:
    engine = BudgetEngine(llm_backend=MyOllamaAdapter(), sessions=MyRedisSessionStore())
    app.dependency_overrides[get_budget_engine] = lambda: engine

Public surface
--------------
- `budget_router`, `app`     : FastAPI router / standalone ASGI app.
- `get_budget_engine`        : FastAPI dependency to override at startup.
- `BudgetEngine`              : conversational engine, for direct construction.
- `DeterministicAdvisor`       : zero-dependency, non-LLM fallback advisor.
- `calculator`                  : deterministic financial math module.
- `Settings`, `get_settings`     : environment configuration.
- Schemas                          : FinancialProfile, ChatRequest, BudgetPlan, etc.

Note: the `budget_advisor.router` *submodule* (router.py) is always
reachable as `budget_advisor.router` regardless of the above — the
`budget_router` re-export here is a separate, deliberately
differently-named alias for the `APIRouter` instance defined inside it,
so importing `budget_advisor` never shadows `import budget_advisor.router`.
"""

from __future__ import annotations

from . import calculator
from .config import LLMProvider, Settings, get_settings

try:  # pragma: no cover - adapters.py is optional if 'groq' isn't installed
    from .adapters import GroqLLMAdapter
except ImportError:
    GroqLLMAdapter = None  # type: ignore[assignment,misc]
from .engine import (
    BudgetEngine,
    DeterministicAdvisor,
    InMemorySessionStore,
    LLMBackend,
    SessionStore,
)
from .router import app, get_budget_engine
from .router import router as budget_router
from .schemas import (
    BudgetingStrategy,
    BudgetPlan,
    CategoryAllocation,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    DiagnosticResponse,
    ExpenseCategory,
    ExpenseItem,
    FinancialGoal,
    FinancialMetrics,
    FinancialProfile,
    MessageRole,
    PlanRequest,
    RiskFlag,
    RiskLevel,
    SessionHistoryResponse,
    SessionResetResponse,
)

__all__ = [
    # app / router / DI
    "app",
    "budget_router",
    "get_budget_engine",
    # engine
    "BudgetEngine",
    "DeterministicAdvisor",
    "LLMBackend",
    "SessionStore",
    "InMemorySessionStore",
    "GroqLLMAdapter",
    # calculator module (imported as a whole; functions accessed as calculator.x)
    "calculator",
    # config
    "Settings",
    "get_settings",
    "LLMProvider",
    # schemas
    "ExpenseCategory",
    "ExpenseItem",
    "FinancialProfile",
    "FinancialGoal",
    "CategoryAllocation",
    "BudgetPlan",
    "BudgetingStrategy",
    "RiskLevel",
    "RiskFlag",
    "FinancialMetrics",
    "DiagnosticResponse",
    "MessageRole",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "PlanRequest",
    "SessionHistoryResponse",
    "SessionResetResponse",
]
