# Budget Advisor Chatbot Microservice (Sentinel Integrated)

Production-ready, ultra-fast asynchronous personal budget coaching and financial planning microservice built with FastAPI, Pydantic v2, and multi-provider LLM support (`AsyncGroq`, `AsyncOpenAI`, `Ollama`).

## Key Features
- **Non-Blocking Async Performance**: Full `AsyncGroq` and `async def` event-loop pipeline preventing server bottlenecks under concurrent judge traffic.
- **Multi-Provider LLM Factory**: Factory pattern supporting `GROQ`, `OPENAI`, and `OLLAMA` with dynamic `.env` provider switching.
- **Deterministic Math Engine**: All numbers (savings rate, emergency runway, cash flow, debt amortization, compound interest) are pre-computed in pure Python before LLM narration.
- **SQLite / Redis Session Persistence**: Turn history persists across server restarts.
- **PII Scrubbing & Guardrails**: Redacts SSNs, credit card numbers, and bank account numbers automatically.
- **Hackathon Demo Presets**: Instant demo profiles (`GET /api/v1/budget/presets`).
- **CORS Enabled**: Ready for instant frontend integration.

## Quick Start

### 1. Install Dependencies

From the repository root, create and activate a virtual environment, then install the project dependencies.

**Windows PowerShell**

```bash
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)

Copy `.env.example` to `.env` inside this folder and set `BUDGET_ADVISOR_LLM_API_KEY` locally. Never commit the `.env` file.

```env
BUDGET_ADVISOR_LLM_PROVIDER=groq
BUDGET_ADVISOR_LLM_API_KEY=your_api_key_here
```

### 3. Run Development Server
```bash
python -m uvicorn stage5_gateway.api:app --host 127.0.0.1 --port 8000 --reload
```

- **Interactive Swagger Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Demo Presets**: [http://127.0.0.1:8000/api/v1/budget/presets](http://127.0.0.1:8000/api/v1/budget/presets)

### 4. Start the Frontend

In a second terminal, run the same command on Windows, macOS, and Linux:

```bash
cd frontend
npm install
npm start
```

Open the local URL printed by Vite (normally `http://127.0.0.1:5173`).

### 5. Run Test Suite
```bash
python -m unittest discover -s sentinel_budget_chatbot/tests -p "test_*.py"
```
