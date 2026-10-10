"""
Tests for budget_advisor.calculator
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone

from sentinel_budget_chatbot import calculator
from sentinel_budget_chatbot.schemas import (
    ExpenseCategory,
    ExpenseItem,
    FinancialGoal,
    FinancialMetrics,
    FinancialProfile,
    RiskLevel,
)


class TestCalculatorMetrics(unittest.TestCase):
    def setUp(self):
        self.expenses = [
            ExpenseItem(name="Rent", amount=1200.0, category=ExpenseCategory.HOUSING, is_fixed=True),
            ExpenseItem(name="Electric", amount=150.0, category=ExpenseCategory.UTILITIES, is_fixed=True),
            ExpenseItem(name="Groceries", amount=400.0, category=ExpenseCategory.FOOD, is_fixed=False),
            ExpenseItem(name="Dining Out", amount=200.0, category=ExpenseCategory.ENTERTAINMENT, is_fixed=False),
            ExpenseItem(name="401k", amount=300.0, category=ExpenseCategory.SAVINGS_INVESTMENT, is_fixed=True),
        ]
        self.profile = FinancialProfile(
            monthly_net_income=4000.0,
            expenses=self.expenses,
            total_savings=9000.0,
            total_debt=12000.0,
            currency="USD",
        )

    def test_total_monthly_expenses(self):
        total = calculator.total_monthly_expenses(self.expenses)
        self.assertEqual(total, 2250.0)

    def test_fixed_vs_discretionary(self):
        fixed, discretionary = calculator.fixed_vs_discretionary(self.expenses)
        self.assertEqual(fixed, 1650.0)
        self.assertEqual(discretionary, 600.0)

    def test_net_cash_flow(self):
        cash_flow = calculator.net_cash_flow(4000.0, self.expenses)
        self.assertEqual(cash_flow, 1750.0)

    def test_savings_rate_pct(self):
        # Net cash flow (1750) + Savings allocation (300) = 2050 / 4000 = 51.25%
        rate = calculator.savings_rate_pct(4000.0, self.expenses)
        self.assertEqual(rate, 51.25)

    def test_savings_rate_zero_income(self):
        rate = calculator.savings_rate_pct(0.0, self.expenses)
        self.assertEqual(rate, 0.0)

    def test_expense_breakdown_pct(self):
        breakdown = calculator.expense_breakdown_pct(4000.0, self.expenses)
        self.assertEqual(breakdown[ExpenseCategory.HOUSING], 30.0)
        self.assertEqual(breakdown[ExpenseCategory.UTILITIES], 3.75)
        self.assertEqual(breakdown[ExpenseCategory.FOOD], 10.0)
        self.assertEqual(breakdown[ExpenseCategory.ENTERTAINMENT], 5.0)
        self.assertEqual(breakdown[ExpenseCategory.SAVINGS_INVESTMENT], 7.5)
        self.assertEqual(breakdown[ExpenseCategory.HEALTHCARE], 0.0)

    def test_emergency_fund_runway_months(self):
        # 9000 total savings / 2250 expenses = 4.0 months
        runway = calculator.emergency_fund_runway_months(9000.0, self.expenses)
        self.assertEqual(runway, 4.0)

    def test_emergency_fund_runway_zero_expenses(self):
        runway = calculator.emergency_fund_runway_months(9000.0, [])
        self.assertIsNone(runway)

    def test_debt_to_income_ratio(self):
        # 12000 debt / (4000 * 12) = 12000 / 48000 = 0.25
        ratio = calculator.debt_to_income_ratio(12000.0, 4000.0)
        self.assertEqual(ratio, 0.25)

    def test_debt_to_income_ratio_zero_income(self):
        ratio = calculator.debt_to_income_ratio(12000.0, 0.0)
        self.assertIsNone(ratio)

    def test_compute_metrics(self):
        metrics = calculator.compute_metrics(self.profile)
        self.assertIsInstance(metrics, FinancialMetrics)
        self.assertEqual(metrics.total_monthly_expenses, 2250.0)
        self.assertEqual(metrics.net_cash_flow, 1750.0)


class TestRiskFlagsAndGoals(unittest.TestCase):
    def test_evaluate_risk_flags_deficit(self):
        expenses = [ExpenseItem(name="Rent", amount=3000.0, category=ExpenseCategory.HOUSING, is_fixed=True)]
        profile = FinancialProfile(monthly_net_income=2000.0, expenses=expenses, total_savings=500.0, total_debt=0.0)
        metrics = calculator.compute_metrics(profile)

        flags = calculator.evaluate_risk_flags(
            profile=profile,
            metrics=metrics,
            housing_risk_threshold_pct=0.30,
            emergency_fund_healthy_months=3.0,
            emergency_fund_ideal_months=6.0,
        )

        critical_flags = [f for f in flags if f.level == RiskLevel.CRITICAL]
        self.assertTrue(len(critical_flags) >= 1)

    def test_required_monthly_contribution(self):
        future_date = datetime.now(timezone.utc) + timedelta(days=365)
        goal = FinancialGoal(
            goal_name="Vacation",
            target_amount=1200.0,
            current_amount=0.0,
            target_date=future_date,
        )
        contrib = calculator.required_monthly_contribution(goal)
        self.assertIsNotNone(contrib)
        self.assertGreater(contrib, 0.0)


class TestAdvancedCalculators(unittest.TestCase):
    def test_compute_debt_payoff_schedule(self):
        res = calculator.compute_debt_payoff_schedule(
            total_debt=5000.0,
            monthly_payment=300.0,
            annual_interest_rate_pct=18.0,
        )
        self.assertTrue(res.months_to_payoff > 0)
        self.assertTrue(res.total_interest_paid > 0)
        self.assertEqual(len(res.monthly_schedule), res.months_to_payoff)

    def test_project_compound_growth(self):
        res = calculator.project_compound_growth(
            monthly_contribution=500.0,
            annual_return_pct=7.0,
            years=5,
            initial_balance=1000.0,
        )
        self.assertEqual(res.years, 5)
        self.assertTrue(res.final_balance > res.total_principal)
        self.assertEqual(len(res.yearly_breakdown), 5)

    def test_inflation_adjusted_future_value(self):
        fv = calculator.inflation_adjusted_future_value(amount=1000.0, years=10, annual_inflation_pct=3.0)
        self.assertGreater(fv, 1000.0)


if __name__ == "__main__":
    unittest.main()
