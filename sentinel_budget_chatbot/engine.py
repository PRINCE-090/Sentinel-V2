"""
budget_advisor.engine
=======================
Conversational retrieval and context-augmentation engine for the Budget
Advisor.

Responsibilities
----------------
1. Run every numeric computation through `calculator.py` first — the LLM
   (if any) only ever narrates numbers it's handed, never computes them.
2. Build a structured system prompt / context bundle from the computed
   metrics, goals, and profile.
3. Delegate text generation to an async `LLMBackend` protocol
   (Groq / OpenAI / Ollama — provider-agnostic by design).
4. If no LLM backend is configured or the backend call fails, fall back
   to `DeterministicAdvisor`, which produces template-based advice from
   the same computed metrics with zero external calls.
5. Maintain per-session conversation history via a pluggable
   `SessionStore` protocol (InMemory / SQLite / Redis).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from . import calculator
from .config import Settings, get_settings
from .schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ExpenseCategory,
    FinancialGoal,
    FinancialMetrics,
    FinancialProfile,
    MessageRole,
    UIWidget,
    WidgetType,
)
from .security import sanitize_user_input, scrub_pii

logger = logging.getLogger("budget_advisor.engine")


# ---------------------------------------------------------------------------
# Pluggable protocols
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMBackend(Protocol):
    """
    Structural contract for any LLM integration (Groq / OpenAI / Ollama).
    Uses asynchronous execution for non-blocking concurrency.
    """

    async def complete(
        self,
        system_prompt: str,
        user_query: str,
        history: List[ChatMessage],
    ) -> str:
        """Returns the assistant's narrative reply as plain text."""
        ...


@runtime_checkable
class SessionStore(Protocol):
    """Persistence boundary for conversational turn history."""

    def append(self, session_id: str, message: ChatMessage) -> None:
        ...

    def get_history(self, session_id: str) -> List[ChatMessage]:
        ...

    def clear(self, session_id: str) -> bool:
        ...


# ---------------------------------------------------------------------------
# Session Store Implementations
# ---------------------------------------------------------------------------

class InMemorySessionStore:
    """Minimal default in-memory SessionStore."""

    def __init__(self, max_turns: int = 50) -> None:
        self._sessions: Dict[str, List[ChatMessage]] = {}
        self._max_turns = max_turns

    def append(self, session_id: str, message: ChatMessage) -> None:
        turns = self._sessions.setdefault(session_id, [])
        turns.append(message)
        if len(turns) > self._max_turns:
            del turns[: len(turns) - self._max_turns]

    def get_history(self, session_id: str) -> List[ChatMessage]:
        return list(self._sessions.get(session_id, []))

    def clear(self, session_id: str) -> bool:
        existed = session_id in self._sessions
        self._sessions.pop(session_id, None)
        return existed


class SQLiteSessionStore:
    """SQLite-backed persistent SessionStore with WAL mode."""

    def __init__(self, db_path: str = "budget_advisor_sessions.db", max_turns: int = 50) -> None:
        self.db_path = db_path
        self._max_turns = max_turns
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")

    def append(self, session_id: str, message: ChatMessage) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, message.role.value, message.content, message.timestamp.isoformat()),
            )
            cursor = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
                (session_id, self._max_turns),
            )
            old_ids = [row[0] for row in cursor.fetchall()]
            if old_ids:
                conn.executemany("DELETE FROM messages WHERE id = ?", [(i,) for i in old_ids])

    def get_history(self, session_id: str) -> List[ChatMessage]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
            messages = []
            for role_str, content, ts_str in cursor.fetchall():
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    ts = datetime.now(timezone.utc)
                messages.append(ChatMessage(role=MessageRole(role_str), content=content, timestamp=ts))
            return messages

    def clear(self, session_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0


class RedisSessionStore:
    """Redis-backed SessionStore implementation for distributed deployments."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0", max_turns: int = 50, ttl_seconds: int = 86400 * 30) -> None:
        try:
            import redis
        except ImportError as exc:
            raise ImportError("The 'redis' package is required to use RedisSessionStore. Install with: pip install redis") from exc

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._max_turns = max_turns
        self._ttl = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"budget_advisor:session:{session_id}"

    def append(self, session_id: str, message: ChatMessage) -> None:
        key = self._key(session_id)
        data = message.model_dump_json()
        self._client.rpush(key, data)
        self._client.ltrim(key, -self._max_turns, -1)
        self._client.expire(key, self._ttl)

    def get_history(self, session_id: str) -> List[ChatMessage]:
        key = self._key(session_id)
        items = self._client.lrange(key, 0, -1)
        messages = []
        for item in items:
            try:
                messages.append(ChatMessage.model_validate_json(item))
            except Exception:
                pass
        return messages

    def clear(self, session_id: str) -> bool:
        key = self._key(session_id)
        return bool(self._client.delete(key))


def generate_ui_widgets(
    profile: Optional[FinancialProfile],
    metrics: Optional[FinancialMetrics],
    goals: Optional[List[FinancialGoal]],
) -> List[UIWidget]:
    """Generates structured UI widget payload hints for frontend rendering."""
    widgets: List[UIWidget] = []
    if profile and metrics:
        breakdown_data = {cat.value: pct for cat, pct in metrics.expense_breakdown_pct.items() if pct > 0}
        if breakdown_data:
            widgets.append(
                UIWidget(
                    widget_type=WidgetType.PIE_CHART,
                    title="Expense Breakdown (% of Income)",
                    data=breakdown_data,
                )
            )

        if profile.total_debt > 0:
            payoff_res = calculator.compute_debt_payoff_schedule(
                total_debt=profile.total_debt,
                monthly_payment=max(metrics.net_cash_flow * 0.5, 100.0),
            )
            widgets.append(
                UIWidget(
                    widget_type=WidgetType.PAYOFF_TIMELINE,
                    title="Estimated Debt Payoff Schedule",
                    data=payoff_res.model_dump(),
                )
            )

        if metrics.net_cash_flow > 0:
            growth_res = calculator.project_compound_growth(
                monthly_contribution=metrics.net_cash_flow,
                annual_return_pct=7.0,
                years=10,
                initial_balance=profile.total_savings,
            )
            widgets.append(
                UIWidget(
                    widget_type=WidgetType.GROWTH_CHART,
                    title="10-Year Wealth Projection (7% Annual Return)",
                    data=growth_res.model_dump(),
                )
            )

        checklist_items = []
        if metrics.net_cash_flow < 0:
            checklist_items.append("Review discretionary spending to eliminate monthly deficit")
        else:
            checklist_items.append("Set up automated monthly transfer for net cash flow savings")

        if (metrics.emergency_fund_runway_months or 0) < 3.0:
            checklist_items.append("Prioritize building a 3-month baseline emergency fund")

        if profile.total_debt > 0:
            checklist_items.append("Apply Debt Avalanche strategy to pay down high-interest debt")

        if checklist_items:
            widgets.append(
                UIWidget(
                    widget_type=WidgetType.ACTION_CHECKLIST,
                    title="Recommended Financial Action Items",
                    data={"items": checklist_items},
                )
            )

    return widgets


# ---------------------------------------------------------------------------
# Deterministic fallback advisor
# ---------------------------------------------------------------------------

class DeterministicAdvisor:
    def generate(
        self,
        query: str,
        history: List[ChatMessage],
        metrics: Optional[FinancialMetrics],
        goals: Optional[List[FinancialGoal]],
        disclaimer: str,
    ) -> str:
        if metrics is None:
            return (
                "**To give you a tailored answer, share:**\n"
                "- Monthly income\n"
                "- Regular expenses\n"
                "- Total savings\n"
                "- Total debt\n\n"
                "**I can then help with:**\n"
                "- Savings rate and monthly cash flow\n"
                "- Emergency-fund runway\n"
                "- Spending and debt-payoff priorities\n\n"
                f"**Note:** {disclaimer}"
            )

        snapshot_lines: List[str] = []

        if metrics.net_cash_flow < 0:
            snapshot_lines.append(
                f"Expenses exceed income by "
                f"{abs(metrics.net_cash_flow):,.2f} per month — that's deficit spending. "
                "The most common first step is reviewing discretionary categories "
                "(entertainment, non-essential subscriptions) before touching fixed costs."
            )
        else:
            snapshot_lines.append(
                f"Net cash flow is {metrics.net_cash_flow:,.2f} per month and the "
                f"current savings rate is {metrics.savings_rate_pct:.1f}% of income."
            )

        if metrics.emergency_fund_runway_months is not None:
            snapshot_lines.append(
                f"Current savings would cover about {metrics.emergency_fund_runway_months:.1f} "
                "months of expenses if income stopped."
            )

        housing_pct = metrics.expense_breakdown_pct.get(ExpenseCategory.HOUSING, 0.0)
        if housing_pct:
            snapshot_lines.append(f"Housing is {housing_pct:.1f}% of income.")

        sections = ["**Financial snapshot**\n" + "\n".join(f"- {line}" for line in snapshot_lines)]

        if goals:
            goal_lines = []
            for goal in goals:
                contribution = calculator.required_monthly_contribution(goal)
                if contribution is None:
                    goal_lines.append(f"'{goal.goal_name}' target date has already passed.")
                else:
                    goal_lines.append(
                        f"'{goal.goal_name}' needs about {contribution:,.2f}/month to stay on track."
                    )
            if goal_lines:
                sections.append("**Goals**\n" + "\n".join(f"- {line}" for line in goal_lines))

        sections.append(
            "**Recommended next step**\n"
            "1. Review the snapshot above and identify the largest discretionary expense.\n"
            "2. Set a realistic monthly amount for savings or debt repayment.\n"
            "3. Share any missing income, expense, savings, or debt details for a more tailored plan."
        )
        sections.append(f"**Note:** {disclaimer}")

        return "\n\n".join(sections)


def build_system_prompt(
    profile: Optional[FinancialProfile],
    metrics: Optional[FinancialMetrics],
    goals: Optional[List[FinancialGoal]],
    disclaimer: str,
) -> str:
    sections = [
        "You are a conversational personal budget advisor and financial "
        "planning coach. You help users understand their finances and make "
        "practical budgeting decisions.",
        "CRITICAL: All financial figures below were computed deterministically "
        "by a separate calculation engine. Treat every number as ground truth. "
        "Do NOT recompute, re-derive, or alter any figure — reference them "
        "exactly as given. If the user asks a question requiring a new "
        "calculation not present below, say the figure isn't available rather "
        "than estimating it yourself.",
        "Be practical, encouraging, and specific. If cash flow is negative or "
        "the user appears financially stressed, respond with gentle, actionable "
        "guidance rather than alarm.",
        "FORMAT: Use concise Markdown that is easy to scan. Start with a short "
        "bold takeaway when useful, group related content under short bold labels, "
        "use '-' bullet lists for recommendations or facts, and use '1.' numbered "
        "lists only for ordered steps. Keep paragraphs to one or two sentences. "
        "Do not put the entire answer into one paragraph.",
        f"Always include this disclaimer at the end of your reply, verbatim: \"{disclaimer}\"",
    ]

    if profile is not None and metrics is not None:
        sections.append(
            "COMPUTED FINANCIAL METRICS:\n"
            f"- Monthly net income: {profile.monthly_net_income:,.2f} {profile.currency}\n"
            f"- Total monthly expenses: {metrics.total_monthly_expenses:,.2f}\n"
            f"- Fixed expenses: {metrics.fixed_expenses_total:,.2f}\n"
            f"- Discretionary expenses: {metrics.discretionary_expenses_total:,.2f}\n"
            f"- Net cash flow: {metrics.net_cash_flow:,.2f}\n"
            f"- Savings rate: {metrics.savings_rate_pct:.2f}%\n"
            f"- Total savings: {profile.total_savings:,.2f}\n"
            f"- Total debt: {profile.total_debt:,.2f}\n"
            f"- Emergency fund runway: "
            f"{metrics.emergency_fund_runway_months if metrics.emergency_fund_runway_months is not None else 'N/A'} months\n"
            f"- Debt-to-income ratio: "
            f"{metrics.debt_to_income_ratio if metrics.debt_to_income_ratio is not None else 'N/A'}"
        )
        breakdown_lines = "\n".join(
            f"  - {cat.value}: {pct:.2f}%"
            for cat, pct in metrics.expense_breakdown_pct.items()
            if pct > 0
        )
        if breakdown_lines:
            sections.append(f"EXPENSE BREAKDOWN (% of income):\n{breakdown_lines}")
    else:
        sections.append(
            "No financial profile has been supplied yet. Ask the user for income, "
            "expenses, savings, and debt if the question requires specific numbers; "
            "otherwise answer generally."
        )

    if goals:
        goal_lines = []
        for goal in goals:
            contribution = calculator.required_monthly_contribution(goal)
            contribution_str = (
                f"{contribution:,.2f}/month needed" if contribution is not None else "target date has passed"
            )
            goal_lines.append(
                f"  - {goal.goal_name}: {goal.current_amount:,.2f} / {goal.target_amount:,.2f} "
                f"by {goal.target_date.date()} ({contribution_str})"
            )
        sections.append("FINANCIAL GOALS:\n" + "\n".join(goal_lines))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class BudgetEngine:
    """
    Orchestrates a single chat turn end-to-end (asynchronous).
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm_backend: Optional[LLMBackend] = None,
        sessions: Optional[SessionStore] = None,
        fallback_advisor: Optional[DeterministicAdvisor] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._llm_backend = llm_backend  # None => always use fallback
        self._sessions = sessions or InMemorySessionStore(
            max_turns=self._settings.session_history_max_turns
        )
        self._fallback = fallback_advisor or DeterministicAdvisor()

    async def handle_message(self, request: ChatRequest) -> ChatResponse:
        warnings: List[str] = []

        scrubbed_query, pii_redactions = scrub_pii(request.query)
        sanitized_query = sanitize_user_input(scrubbed_query)

        try:
            self._sessions.append(
                request.session_id,
                ChatMessage(role=MessageRole.USER, content=sanitized_query),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist inbound user message.")
            warnings.append("Session history could not be persisted for this turn.")

        metrics: Optional[FinancialMetrics] = None
        if request.profile is not None:
            try:
                metrics = calculator.compute_metrics(request.profile)
                if request.profile.monthly_net_income == 0:
                    warnings.append(
                        "Monthly net income is 0 — percentage-based metrics "
                        "(savings rate, expense breakdown) are not meaningful "
                        "until income is provided."
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Metric computation failed for session_id=%s", request.session_id)
                warnings.append(
                    "Could not compute financial metrics from the supplied profile; "
                    "responding without quantitative grounding."
                )

        combined_history = self._dedupe_history(
            list(request.history) + self._sessions.get_history(request.session_id)
        )

        reply, gen_warnings = await self._generate_reply(
            query=sanitized_query,
            history=combined_history,
            profile=request.profile,
            metrics=metrics,
            goals=request.goals,
        )
        warnings.extend(gen_warnings)

        try:
            self._sessions.append(
                request.session_id,
                ChatMessage(role=MessageRole.ASSISTANT, content=reply),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist outbound assistant message.")
            warnings.append("Assistant reply could not be persisted to session history.")

        widgets = generate_ui_widgets(request.profile, metrics, request.goals)
        financial_metrics_dict: Dict[str, Any] = metrics.model_dump() if metrics else {}

        return ChatResponse(
            session_id=request.session_id,
            reply=reply,
            financial_metrics=financial_metrics_dict,
            warnings=warnings,
            widgets=widgets,
            pii_redactions=pii_redactions,
        )

    def get_history(self, session_id: str) -> List[ChatMessage]:
        return self._sessions.get_history(session_id)

    def reset_session(self, session_id: str) -> bool:
        return self._sessions.clear(session_id)

    async def _generate_reply(
        self,
        query: str,
        history: List[ChatMessage],
        profile: Optional[FinancialProfile],
        metrics: Optional[FinancialMetrics],
        goals: Optional[List[FinancialGoal]],
    ) -> tuple[str, List[str]]:
        warnings: List[str] = []
        disclaimer = self._settings.disclaimer_text

        if self._llm_backend is not None:
            try:
                system_prompt = build_system_prompt(profile, metrics, goals, disclaimer)
                reply = await self._llm_backend.complete(
                    system_prompt=system_prompt,
                    user_query=query,
                    history=history,
                )
                if disclaimer not in reply:
                    reply = f"{reply}\n\n{disclaimer}"
                return reply, warnings
            except Exception:  # noqa: BLE001
                logger.exception("LLM backend call failed; falling back to deterministic advisor.")
                warnings.append(
                    "The configured language model backend was unavailable; "
                    "responded using deterministic template-based guidance instead."
                )

        reply = self._fallback.generate(
            query=query,
            history=history,
            metrics=metrics,
            goals=goals,
            disclaimer=disclaimer,
        )
        return reply, warnings

    @staticmethod
    def _dedupe_history(history: List[ChatMessage]) -> List[ChatMessage]:
        seen: set[tuple[str, str, str]] = set()
        deduped: List[ChatMessage] = []
        for msg in history:
            key = (msg.role.value, msg.content, msg.timestamp.isoformat())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(msg)
        return deduped
