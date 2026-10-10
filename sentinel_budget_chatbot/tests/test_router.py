"""
Integration tests for FastAPI REST Router (router.py)
"""

import unittest

from fastapi.testclient import TestClient
from sentinel_budget_chatbot.router import app


class TestRouterEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sample_profile = {
            "monthly_net_income": 5000.0,
            "expenses": [
                {"name": "Rent", "amount": 1500.0, "category": "HOUSING", "is_fixed": True},
                {"name": "Groceries", "amount": 500.0, "category": "FOOD", "is_fixed": False},
            ],
            "total_savings": 10000.0,
            "total_debt": 2000.0,
            "currency": "USD",
        }

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_analyze_endpoint(self):
        response = self.client.post("/api/v1/budget/analyze", json=self.sample_profile)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("metrics", data)
        self.assertEqual(data["metrics"]["total_monthly_expenses"], 2000.0)
        self.assertEqual(data["metrics"]["net_cash_flow"], 3000.0)

    def test_plan_endpoint(self):
        payload = {
            "profile": self.sample_profile,
            "goals": [],
            "preferred_strategy": "50_30_20",
        }
        response = self.client.post("/api/v1/budget/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["strategy_used"], "50_30_20")
        self.assertIn("recommended_allocations", data)

    def test_chat_endpoint_deterministic_fallback(self):
        payload = {
            "session_id": "test-session-123",
            "query": "How is my financial health looking?",
            "profile": self.sample_profile,
        }
        response = self.client.post("/api/v1/budget/chat", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["session_id"], "test-session-123")
        self.assertIn("reply", data)
        self.assertIn("3,000.00", data["reply"])  # deterministic cash flow check

    def test_session_history_and_reset(self):
        session_id = "test-session-456"
        self.client.post(
            "/api/v1/budget/chat",
            json={"session_id": session_id, "query": "Hello budget coach!"},
        )

        hist_resp = self.client.get(f"/api/v1/budget/sessions/{session_id}")
        self.assertEqual(hist_resp.status_code, 200)
        self.assertTrue(len(hist_resp.json()["messages"]) >= 2)

        reset_resp = self.client.delete(f"/api/v1/budget/sessions/{session_id}")
        self.assertEqual(reset_resp.status_code, 200)
        self.assertTrue(reset_resp.json()["cleared"])

        hist_resp_after = self.client.get(f"/api/v1/budget/sessions/{session_id}")
        self.assertEqual(len(hist_resp_after.json()["messages"]), 0)

    def test_presets_endpoint(self):
        response = self.client.get("/api/v1/budget/presets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("young_professional", data)
        self.assertIn("family_in_deficit", data)
        self.assertIn("freelancer_building_buffer", data)


if __name__ == "__main__":
    unittest.main()
