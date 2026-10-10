"""
budget_advisor.calculator
===========================
Deterministic financial math engine.

Every number that appears in a chat reply, diagnostic report, or budget
plan MUST originate here — never from LLM free-text generation. This
keeps the assistant from hallucinating arithmetic (a well-documented
LLM failure mode) on numbers that matter to a user's real finances.

Pure Python, stdlib only. No I/O, no network, no framework dependency —
trivially unit-testable in isolation.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .schemas import (
    BudgetingStrategy,
    CompoundGrowthResult,
    CompoundGrowthYear,
    DebtPayoffMonth,
    DebtPayoffResult,
    ExpenseCategory,
    ExpenseItem,
    FinancialGoal,
    FinancialMetrics,
    FinancialProfile,
    RiskFlag,
    RiskLevel,
)


# ---------------------------------------------------------------------------
# Core metric computation
# ---------------------------------------------------------------------------

def total_monthly_expenses(expenses: List[ExpenseItem]) -> float:
    """Sum of all expense line items."""
    return round(sum(item.amount for item in expenses), 2)


def fixed_vs_discretionary(expenses: List[ExpenseItem]) -> tuple[float, float]:
    """Returns (fixed_total, discretionary_total)."""
    fixed = round(sum(item.amount for item in expenses if item.is_fixed), 2)
    discretionary = round(sum(item.amount for item in expenses if not item.is_fixed), 2)
    return fixed, discretionary


def net_cash_flow(monthly_net_income: float, expenses: List[ExpenseItem]) -> float:
    """Income minus total expenses. Negative indicates deficit spending."""
    return round(monthly_net_income - total_monthly_expenses(expenses), 2)


def savings_rate_pct(
    monthly_net_income: float,
    expenses: List[ExpenseItem],
) -> float:
    """
    Savings Rate (%) = (Net Cash Flow + Savings/Investment Allocations) / Income * 100

    Net Cash Flow already excludes SAVINGS_INVESTMENT-category spend from
    "consumed" income only in the sense that such spend is itself a form
    of saving; per the spec, savings allocations are added back to net
    cash flow before dividing by income, since money routed to
    SAVINGS_INVESTMENT is also subtracted out when computing total
    expenses. This avoids double-penalizing users who budget savings as
    a line item.

    Returns 0.0 (not an error) when income is zero, since a percentage of
    zero income is not meaningfully definable but callers should not have
    to special-case a ZeroDivisionError for a plausible zero-income input
    (e.g. a user modeling a no-income scenario).
    """
    if monthly_net_income <= 0:
        return 0.0

    cash_flow = net_cash_flow(monthly_net_income, expenses)
    savings_allocations = round(
        sum(item.amount for item in expenses if item.category == ExpenseCategory.SAVINGS_INVESTMENT),
        2,
    )
    rate = (cash_flow + savings_allocations) / monthly_net_income * 100
    return round(rate, 2)


def expense_breakdown_pct(
    monthly_net_income: float,
    expenses: List[ExpenseItem],
) -> Dict[ExpenseCategory, float]:
    """
    Percentage of income spent per category. Every enum member is present
    in the result (0.0 if unused) so downstream consumers (LLM prompt,
    risk flagging, UI) never need to guess about missing keys.

    Returns all-zero breakdown when income is zero rather than raising,
    since "0% of $0 income" is a coherent (if degenerate) answer.
    """
    breakdown: Dict[ExpenseCategory, float] = {cat: 0.0 for cat in ExpenseCategory}
    if monthly_net_income <= 0:
        return breakdown

    totals: Dict[ExpenseCategory, float] = {cat: 0.0 for cat in ExpenseCategory}
    for item in expenses:
        totals[item.category] += item.amount

    for cat, amount in totals.items():
        breakdown[cat] = round((amount / monthly_net_income) * 100, 2)

    return breakdown


def emergency_fund_runway_months(
    total_savings: float,
    expenses: List[ExpenseItem],
) -> Optional[float]:
    """
    Total Savings / Total Monthly Expenses, in months.

    Returns None when monthly expenses are zero — runway is undefined
    (mathematically infinite) rather than a misleading large number or a
    ZeroDivisionError. Callers should render None as "N/A" or
    "no recurring expenses to compare against."
    """
    monthly_expenses = total_monthly_expenses(expenses)
    if monthly_expenses <= 0:
        return None
    return round(total_savings / monthly_expenses, 2)


def debt_to_income_ratio(total_debt: float, monthly_net_income: float) -> Optional[float]:
    """
    Total debt / annualized net income, expressed as a ratio (not a
    percentage) — e.g. 0.35 means total debt equals 35% of one year's
    net income. Returns None when income is zero (ratio undefined).
    """
    if monthly_net_income <= 0:
        return None
    annual_income = monthly_net_income * 12
    if annual_income <= 0:
        return None
    return round(total_debt / annual_income, 4)


def compute_metrics(profile: FinancialProfile) -> FinancialMetrics:
    """Single entrypoint that computes the full FinancialMetrics bundle."""
    fixed, discretionary = fixed_vs_discretionary(profile.expenses)
    return FinancialMetrics(
        total_monthly_expenses=total_monthly_expenses(profile.expenses),
        fixed_expenses_total=fixed,
        discretionary_expenses_total=discretionary,
        net_cash_flow=net_cash_flow(profile.monthly_net_income, profile.expenses),
        savings_rate_pct=savings_rate_pct(profile.monthly_net_income, profile.expenses),
        expense_breakdown_pct=expense_breakdown_pct(profile.monthly_net_income, profile.expenses),
        emergency_fund_runway_months=emergency_fund_runway_months(
            profile.total_savings, profile.expenses
        ),
        debt_to_income_ratio=debt_to_income_ratio(profile.total_debt, profile.monthly_net_income),
    )


# ---------------------------------------------------------------------------
# Risk flagging / benchmark comparison (used by the /analyze endpoint)
# ---------------------------------------------------------------------------

def evaluate_risk_flags(
    profile: FinancialProfile,
    metrics: FinancialMetrics,
    housing_risk_threshold_pct: float,
    emergency_fund_healthy_months: float,
    emergency_fund_ideal_months: float,
) -> List[RiskFlag]:
    """
    Deterministic rule-based risk flags. All thresholds are passed in
    (sourced from Settings) rather than hardcoded, so deployments can
    tune sensitivity without touching this module.
    """
    flags: List[RiskFlag] = []

    # --- Deficit spending ---------------------------------------------------
    if metrics.net_cash_flow < 0:
        flags.append(
            RiskFlag(
                level=RiskLevel.CRITICAL,
                message=(
                    "Expenses exceed income this month (deficit spending). "
                    "Consider reviewing discretionary categories first before "
                    "cutting fixed costs, which are usually harder to change quickly."
                ),
                metric_value=metrics.net_cash_flow,
                benchmark_value=0.0,
            )
        )
    elif metrics.net_cash_flow == 0:
        flags.append(
            RiskFlag(
                level=RiskLevel.WATCH,
                message="Income exactly covers expenses with no margin for savings or surprises.",
                metric_value=metrics.net_cash_flow,
                benchmark_value=0.0,
            )
        )

    # --- Housing overspend ---------------------------------------------------
    housing_pct = metrics.expense_breakdown_pct.get(ExpenseCategory.HOUSING, 0.0) / 100
    if housing_pct > housing_risk_threshold_pct:
        flags.append(
            RiskFlag(
                category=ExpenseCategory.HOUSING,
                level=RiskLevel.AT_RISK,
                message=(
                    f"Housing costs are {housing_pct * 100:.1f}% of income, above the "
                    f"commonly recommended {housing_risk_threshold_pct * 100:.0f}% guideline."
                ),
                metric_value=round(housing_pct * 100, 2),
                benchmark_value=round(housing_risk_threshold_pct * 100, 2),
            )
        )

    # --- Emergency fund runway ------------------------------------------------
    runway = metrics.emergency_fund_runway_months
    if runway is None:
        flags.append(
            RiskFlag(
                level=RiskLevel.OK,
                message="No recurring monthly expenses recorded, so emergency-fund "
                "runway cannot be computed.",
            )
        )
    elif runway < emergency_fund_healthy_months:
        flags.append(
            RiskFlag(
                level=RiskLevel.AT_RISK if runway < emergency_fund_healthy_months / 2 else RiskLevel.WATCH,
                message=(
                    f"Emergency fund covers about {runway:.1f} months of expenses, "
                    f"below the commonly recommended {emergency_fund_healthy_months:.0f}-month minimum."
                ),
                metric_value=runway,
                benchmark_value=emergency_fund_healthy_months,
            )
        )
    elif runway < emergency_fund_ideal_months:
        flags.append(
            RiskFlag(
                level=RiskLevel.OK,
                message=(
                    f"Emergency fund covers about {runway:.1f} months — above the minimum, "
                    f"approaching the {emergency_fund_ideal_months:.0f}-month ideal."
                ),
                metric_value=runway,
                benchmark_value=emergency_fund_ideal_months,
            )
        )

    # --- Savings rate ----------------------------------------------------------
    if metrics.savings_rate_pct < 0:
        flags.append(
            RiskFlag(
                level=RiskLevel.CRITICAL,
                message="Negative savings rate — savings/investment balances are being drawn "
                "down faster than income covers spending.",
                metric_value=metrics.savings_rate_pct,
            )
        )
    elif metrics.savings_rate_pct < 10:
        flags.append(
            RiskFlag(
                level=RiskLevel.WATCH,
                message=f"Savings rate is {metrics.savings_rate_pct:.1f}%, below a typical 10-20% target range.",
                metric_value=metrics.savings_rate_pct,
                benchmark_value=20.0,
            )
        )

    return flags


def benchmark_comparison(
    metrics: FinancialMetrics,
    needs_pct: float,
    wants_pct: float,
    savings_pct: float,
) -> Dict[str, object]:
    """
    Compares the profile's actual needs/wants/savings split against the
    classic 50/30/20 benchmark. "Needs" = fixed expenses; "wants" =
    discretionary non-savings expenses; "savings" = net cash flow +
    savings-category spend (mirrors savings_rate_pct definition).
    """
    total = metrics.total_monthly_expenses
    income_basis = metrics.fixed_expenses_total + metrics.discretionary_expenses_total

    actual_needs_pct = (
        round((metrics.fixed_expenses_total / income_basis) * 100, 2) if income_basis > 0 else 0.0
    )
    actual_wants_pct = (
        round((metrics.discretionary_expenses_total / income_basis) * 100, 2) if income_basis > 0 else 0.0
    )

    return {
        "rule": "50/30/20",
        "benchmark": {
            "needs_pct": round(needs_pct * 100, 1),
            "wants_pct": round(wants_pct * 100, 1),
            "savings_pct": round(savings_pct * 100, 1),
        },
        "actual": {
            "needs_pct": actual_needs_pct,
            "wants_pct": actual_wants_pct,
            "savings_pct": metrics.savings_rate_pct,
        },
        "total_monthly_expenses": total,
    }


# ---------------------------------------------------------------------------
# Goal projection helpers (used by both engine.py and router.py's /plan)
# ---------------------------------------------------------------------------

def months_until(target_date, from_date=None) -> float:
    """
    Whole/fractional months between now (or `from_date`) and a target
    datetime. Returns 0.0 for past-due dates rather than a negative
    number, since "months remaining" bottoms out at zero for planning
    purposes (the caller can separately flag the goal as past-due).
    """
    from datetime import datetime, timezone

    if from_date is None:
        from_date = datetime.now(timezone.utc)
    if target_date.tzinfo is None:
        target_date = target_date.replace(tzinfo=timezone.utc)
    if from_date.tzinfo is None:
        from_date = from_date.replace(tzinfo=timezone.utc)

    delta_days = (target_date - from_date).total_seconds() / 86400
    if delta_days <= 0:
        return 0.0
    return round(delta_days / 30.4375, 2)  # average days/month


def required_monthly_contribution(goal: FinancialGoal) -> Optional[float]:
    """
    Monthly savings needed to hit a goal by its target_date, given
    current progress. Returns None if the goal is already past-due
    (months_until == 0) — caller should treat that as "overdue" rather
    than divide by zero.
    """
    months_remaining = months_until(goal.target_date)
    if months_remaining <= 0:
        return None
    remaining_amount = max(goal.target_amount - goal.current_amount, 0.0)
    if remaining_amount == 0:
        return 0.0
    return round(remaining_amount / months_remaining, 2)


# ---------------------------------------------------------------------------
# Advanced Calculators: Debt Amortization & Compound Interest Growth
# ---------------------------------------------------------------------------

def compute_debt_payoff_schedule(
    total_debt: float,
    monthly_payment: float,
    annual_interest_rate_pct: float = 18.0,
    strategy: BudgetingStrategy = BudgetingStrategy.DEBT_AVALANCHE,
    max_months: int = 360,
) -> DebtPayoffResult:
    """
    Computes a month-by-month debt amortization schedule for a given total
    debt balance and monthly contribution payment.
    """
    if total_debt <= 0 or monthly_payment <= 0:
        return DebtPayoffResult(
            strategy=strategy,
            months_to_payoff=0,
            total_interest_paid=0.0,
            total_amount_paid=0.0,
            monthly_schedule=[],
        )

    monthly_rate = (annual_interest_rate_pct / 100.0) / 12.0
    balance = total_debt
    schedule: List[DebtPayoffMonth] = []
    total_interest = 0.0
    total_paid = 0.0
    month = 0

    while balance > 0 and month < max_months:
        month += 1
        interest_charge = round(balance * monthly_rate, 2)
        # If payment doesn't even cover interest, debt is compounding indefinitely
        if monthly_payment <= interest_charge and month == 1:
            # Pay interest plus small principal reduction cap to avoid infinite loop
            payment = interest_charge + 1.0
        else:
            payment = min(monthly_payment, balance + interest_charge)

        principal_paid = round(payment - interest_charge, 2)
        balance = max(round(balance - principal_paid, 2), 0.0)
        total_interest = round(total_interest + interest_charge, 2)
        total_paid = round(total_paid + payment, 2)

        schedule.append(
            DebtPayoffMonth(
                month=month,
                remaining_balance=balance,
                interest_paid=interest_charge,
                principal_paid=principal_paid,
            )
        )

    return DebtPayoffResult(
        strategy=strategy,
        months_to_payoff=month,
        total_interest_paid=total_interest,
        total_amount_paid=total_paid,
        monthly_schedule=schedule,
    )


def project_compound_growth(
    monthly_contribution: float,
    annual_return_pct: float = 7.0,
    years: int = 10,
    initial_balance: float = 0.0,
) -> CompoundGrowthResult:
    """
    Projects investment growth over time with recurring monthly contributions
    and annual return compounding.
    """
    monthly_rate = (annual_return_pct / 100.0) / 12.0
    total_balance = float(initial_balance)
    total_principal = float(initial_balance)
    yearly_breakdown: List[CompoundGrowthYear] = []

    for year in range(1, years + 1):
        for _ in range(12):
            total_balance += monthly_contribution
            total_principal += monthly_contribution
            interest_month = total_balance * monthly_rate
            total_balance += interest_month

        total_interest = max(round(total_balance - total_principal, 2), 0.0)
        yearly_breakdown.append(
            CompoundGrowthYear(
                year=year,
                total_balance=round(total_balance, 2),
                total_principal=round(total_principal, 2),
                total_interest=total_interest,
            )
        )

    final_balance = round(total_balance, 2)
    total_principal = round(total_principal, 2)
    total_interest_earned = max(round(final_balance - total_principal, 2), 0.0)

    return CompoundGrowthResult(
        monthly_contribution=monthly_contribution,
        annual_return_pct=annual_return_pct,
        years=years,
        final_balance=final_balance,
        total_principal=total_principal,
        total_interest_earned=total_interest_earned,
        yearly_breakdown=yearly_breakdown,
    )


def inflation_adjusted_future_value(
    amount: float,
    years: float,
    annual_inflation_pct: float = 2.5,
) -> float:
    """
    Adjusts a monetary target for inflation over a given number of years.
    """
    if years <= 0 or amount <= 0:
        return round(amount, 2)
    future_val = amount * ((1.0 + (annual_inflation_pct / 100.0)) ** years)
    return round(future_val, 2)
