# Frontend Integration Guide (Budget Advisor Chatbot)

This guide provides full API contracts, TypeScript definitions, and code snippets for integrating the **Budget Advisor Chatbot** into your frontend app (React, Next.js, Vue, Svelte, or React Native).

---

## 🚀 Quick Setup for Frontend Developers

1. **Server Base URL**: `http://localhost:8001/api/v1/budget`
2. **Interactive Swagger Docs**: `http://localhost:8001/docs`
3. **CORS**: Pre-enabled for all origins (`*`). No proxy configuration needed.

---

## 📡 API Endpoints

### 1. Conversational Chat (`POST /api/v1/budget/chat`)

Send user queries along with optional financial profiles and goals. Returns AI guidance, deterministic metrics, and UI widget data.

#### Request Body
```json
{
  "session_id": "unique-session-uuid",
  "query": "How is my financial health looking?",
  "profile": {
    "monthly_net_income": 5000.0,
    "expenses": [
      {"name": "Rent", "amount": 1500.0, "category": "HOUSING", "is_fixed": true},
      {"name": "Groceries", "amount": 500.0, "category": "FOOD", "is_fixed": false}
    ],
    "total_savings": 8000.0,
    "total_debt": 2000.0,
    "currency": "USD"
  },
  "goals": [
    {
      "goal_name": "Emergency Fund",
      "target_amount": 10000.0,
      "current_amount": 8000.0,
      "target_date": "2026-12-31T00:00:00Z"
    }
  ]
}
```

#### Response Body
```json
{
  "session_id": "unique-session-uuid",
  "reply": "Your net cash flow is $3,000.00 per month...",
  "financial_metrics": {
    "total_monthly_expenses": 2000.0,
    "net_cash_flow": 3000.0,
    "savings_rate_pct": 60.0,
    "emergency_fund_runway_months": 4.0
  },
  "warnings": [],
  "widgets": [
    {
      "widget_type": "PIE_CHART",
      "title": "Expense Breakdown (% of Income)",
      "data": { "HOUSING": 30.0, "FOOD": 10.0 }
    },
    {
      "widget_type": "ACTION_CHECKLIST",
      "title": "Recommended Financial Action Items",
      "data": { "items": ["Set up automated monthly transfer for net cash flow savings"] }
    }
  ],
  "pii_redactions": []
}
```

---

### 2. Demo Presets (`GET /api/v1/budget/presets`)

Use this endpoint to instantly populate sample profiles into your frontend state for quick demoing!

#### Response
```json
{
  "young_professional": { ... },
  "family_in_deficit": { ... },
  "freelancer_building_buffer": { ... }
}
```

---

## 🔷 TypeScript Types

```typescript
export type ExpenseCategory =
  | 'HOUSING'
  | 'UTILITIES'
  | 'FOOD'
  | 'TRANSPORTATION'
  | 'HEALTHCARE'
  | 'DEBT_PAYMENT'
  | 'ENTERTAINMENT'
  | 'SAVINGS_INVESTMENT'
  | 'OTHER';

export interface ExpenseItem {
  name: string;
  amount: number;
  category: ExpenseCategory;
  is_fixed: boolean;
}

export interface FinancialProfile {
  monthly_net_income: number;
  expenses: ExpenseItem[];
  total_savings: number;
  total_debt: number;
  currency?: string;
}

export interface UIWidget {
  widget_type: 'PIE_CHART' | 'PAYOFF_TIMELINE' | 'GROWTH_CHART' | 'ACTION_CHECKLIST';
  title: string;
  data: Record<string, any>;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  financial_metrics: Record<string, any>;
  warnings: string[];
  widgets: UIWidget[];
  pii_redactions: string[];
}
```

---

## 💻 Example React / Fetch Implementation

```javascript
async function sendChatMessage(query, profile = null) {
  const response = await fetch('http://localhost:8001/api/v1/budget/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: localStorage.getItem('chat_session_id') || crypto.randomUUID(),
      query: query,
      profile: profile
    })
  });

  const data = await response.json();
  return data;
}
```
