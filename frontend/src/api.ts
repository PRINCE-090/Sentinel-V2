import type { CaseDetail, CaseListResponse, EvaluationResponse, ReviewStatus, TransactionRequest } from "./types";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");

export class SentinelApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "SentinelApiError";
  }
}

export type BudgetChatResponse = {
  session_id: string;
  reply: string;
  warnings: string[];
};

export async function sendBudgetChatMessage(
  sessionId: string,
  query: string,
): Promise<BudgetChatResponse> {
  let response: Response;

  try {
    response = await fetch(`${apiBaseUrl}/v1/budget/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, query }),
    });
  } catch {
    throw new SentinelApiError(
      "Could not reach the Budget Advisor service. Confirm the Sentinel gateway is running and try again.",
    );
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new SentinelApiError(
      detail ?? `The Budget Advisor returned ${response.status}. Please try again.`,
      response.status,
    );
  }

  return response.json() as Promise<BudgetChatResponse>;
}

export async function evaluateTransaction(
  request: TransactionRequest,
): Promise<EvaluationResponse> {
  let response: Response;

  try {
    response = await fetch(`${apiBaseUrl}/v1/transactions/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  } catch {
    throw new SentinelApiError(
      "Could not reach the Sentinel API. Confirm the gateway is running and try again.",
    );
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new SentinelApiError(
      detail ?? `The Sentinel API returned ${response.status}. Please try again.`,
      response.status,
    );
  }

  return response.json() as Promise<EvaluationResponse>;
}

async function caseRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, init);
  } catch {
    throw new SentinelApiError("Could not reach the Sentinel API. Confirm the gateway is running and try again.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new SentinelApiError(typeof body?.detail === "string" ? body.detail : `The Sentinel API returned ${response.status}.`, response.status);
  }
  return response.json() as Promise<T>;
}

export function getCases(filters: { review_status?: string; action?: string; priority?: string; min_fraud_score?: number; max_fraud_score?: number; limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => { if (value !== undefined && value !== "") query.set(key, String(value)); });
  return caseRequest<CaseListResponse>(`/v1/cases${query.size ? `?${query}` : ""}`);
}

export function updateCaseReview(transactionId: string, payload: { review_status: ReviewStatus; investigator_note: string }) {
  return caseRequest<CaseDetail>(`/v1/cases/${encodeURIComponent(transactionId)}/review`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export async function downloadCaseExport(transactionId: string, format: "pdf" | "csv") {
  const response = await fetch(`${apiBaseUrl}/v1/cases/${encodeURIComponent(transactionId)}/export?format=${format}`);
  if (!response.ok) throw new SentinelApiError(`The case export could not be created (${response.status}).`, response.status);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = `sentinel-case-${transactionId}.${format}`; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
