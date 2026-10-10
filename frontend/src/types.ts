export type ActionVerdict = "BLOCK" | "HOLD" | "MONITOR" | "RELEASE";
export type ConfidenceBand = "high" | "medium" | "low";
export interface TransactionRequest {
  transaction_id: string;
  sender_account: string;
  receiver_account: string;
  amount: number;
  timestamp: string;
  channel: "UPI" | "NEFT" | "RTGS" | "SWIFT" | "CARD" | "CASH" | string;
  device_fingerprint_hash?: string;
  ip_geolocation?: string;
  merchant_category_code?: string;
}
export interface EvaluationResponse {
  transaction_id: string;
  action: ActionVerdict;
  fraud_score: number;
  primary_reason: string;
  requires_manual_review: boolean;
  sar_narrative: string | null;
}
export interface ReasonCode {
  feature_name: string;
  label: string;
  value: number;
  impact: "positive" | "negative";
  attribution_score: number;
  human_description: string;
}
export interface TypologyMatch {
  typology_name: string;
  fatf_code: string;
  confidence: number;
  description: string;
}
export interface ProceedsHop {
  hop_number: number;
  from_account: string;
  to_account: string;
  amount: number;
  timestamp_delta_hours: number;
  account_metadata?: Record<string, string>;
}
export interface GraphSummary {
  mule_ring_flag: boolean;
  centrality_score: number;
  cycle_detected: boolean;
  cycle_nodes: string[];
  neighbor_count: number;
}
export interface FullDossier {
  detection_result: {
    lightgbm_prob: number;
    anomaly_score: number;
    drift_stage: string;
    confidence_band: ConfidenceBand;
  };
  graph_summary: GraphSummary;
  top_reason_codes: ReasonCode[];
  typology_matches: TypologyMatch[];
  proceeds_trail: ProceedsHop[];
}

export type ReviewStatus = "Suspected" | "Verified" | "Needs Review";
export type InvestigationPriority = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface CaseDetail {
  transaction_id: string;
  action: ActionVerdict;
  fraud_score: number;
  primary_reason: string;
  requires_manual_review: boolean;
  sar_narrative: string | null;
  investigation_priority: InvestigationPriority;
  review_status: ReviewStatus;
  investigator_note: string | null;
  updated_at: string | null;
}

export interface CaseListResponse {
  items: CaseDetail[];
  total: number;
  limit: number;
  offset: number;
}
