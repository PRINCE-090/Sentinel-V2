# Sentinel V2

Sentinel V2 is a local AML investigation workspace with an integrated budget-advisor chatbot.

## Run on Windows

### Prerequisites

- Python 3.12 or newer
- Node.js 20 or newer
- npm

### 1. Create the Python environment

Open PowerShell in the repository folder and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run this once for the current terminal and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 2. Configure the chatbot

Copy `sentinel_budget_chatbot\.env.example` to `sentinel_budget_chatbot\.env`, then set your provider key locally:

```env
BUDGET_ADVISOR_LLM_PROVIDER=groq
BUDGET_ADVISOR_LLM_API_KEY=your_api_key_here
```

Never commit the `.env` file. If no LLM provider is available, the chatbot still responds with its deterministic financial-advice fallback.

### 3. Start the API

Keep the virtual environment active and run:

```powershell
python -m uvicorn stage5_gateway.api:app --host 127.0.0.1 --port 8000 --reload
```

The API is available at `http://127.0.0.1:8000`, with API docs at `http://127.0.0.1:8000/docs`.

### 4. Start the frontend

Open a second PowerShell window in the repository folder:

```powershell
cd frontend
npm install
npm start
```

Open the local URL printed by Vite—normally `http://127.0.0.1:5173`.

### 5. Stop the app

Press `Ctrl+C` in each PowerShell window.

## Verification

```powershell
cd frontend
npm run build
```

```powershell
python -m unittest discover -s sentinel_budget_chatbot/tests -p "test_*.py"
```
